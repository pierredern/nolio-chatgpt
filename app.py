import os
import secrets
import base64
import requests
import math
from statistics import mean

from flask import Flask, redirect, request, session, jsonify

app = Flask(__name__)
app.secret_key = os.environ["FLASK_SECRET_KEY"]

BASE_URL = "https://www.nolio.io"
CLIENT_ID = "5BXkbpFyUlgR3U6Hoiw7hK9y1E0qRelTwpnl55rZ"
CLIENT_SECRET = os.environ["NOLIO_CLIENT_SECRET"]
REDIRECT_URI = os.environ["NOLIO_REDIRECT_URI"]


@app.route("/")
def home():
    return """
    <h1>Nolio ↔ ChatGPT</h1>
    <p><a href="/login">Connecter mon compte Nolio</a></p>
    <p><a href="/user">Voir mon profil Nolio</a></p>
    <p><a href="/sessions">Voir mes séances</a></p>
    <p><a href="/analyse/derniere-seance">Analyser ma dernière séance</a></p>
    """


@app.route("/login")
def login():
    state = secrets.token_urlsafe(32)
    session["oauth_state"] = state

    url = (
        f"{BASE_URL}/api/authorize/"
        f"?response_type=code"
        f"&client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&state={state}"
    )

    return redirect(url)


@app.route("/callback")
def callback():
    if request.args.get("state") != session.get("oauth_state"):
        return "Erreur : état OAuth invalide.", 400

    code = request.args.get("code")

    if not code:
        return "Erreur : aucun code reçu.", 400

    credentials = f"{CLIENT_ID}:{CLIENT_SECRET}".encode()
    basic_auth = base64.b64encode(credentials).decode()

    response = requests.post(
        f"{BASE_URL}/api/token/",
        headers={
            "Authorization": f"Basic {basic_auth}"
        },
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
        },
        timeout=30,
    )

    response.raise_for_status()

    tokens = response.json()

    session["access_token"] = tokens["access_token"]
    session["refresh_token"] = tokens["refresh_token"]

    return redirect("/")


def nolio_get(path, params=None):
    access_token = session.get("access_token")

    if not access_token:
        return None, redirect("/login")

    response = requests.get(
        f"{BASE_URL}{path}",
        headers={
            "Authorization": f"Bearer {access_token}"
        },
        params=params,
        timeout=30,
    )

    if response.status_code == 401:
        return None, ("Token expiré. Il faudra le rafraîchir.", 401)

    response.raise_for_status()

    return response.json(), None


@app.route("/user")
def user():
    data, error = nolio_get("/api/get/user/")

    if error:
        return error

    return jsonify(data)


@app.route("/sessions")
def sessions():
    """
    Récupère les séances réalisées du compte connecté.
    """

    data, error = nolio_get(
        "/api/get/training/",
        params={"limit": 20}
    )

    if error:
        return error

    return jsonify(data)


@app.route("/session/<int:training_id>/streams")
def session_streams(training_id):
    data, error = nolio_get(
        "/api/get/training/streams/",
        params={"id": training_id}
    )

    if error:
        return error

    return jsonify(data)


# ============================================================
# OUTILS D'ANALYSE
# ============================================================

def flatten_streams(data):
    """
    Transforme différentes structures possibles de réponse
    Nolio en dictionnaire :

        {
            "stream_power": [...],
            "stream_heart_rate": [...],
            ...
        }

    On reste volontairement tolérant car la structure exacte
    peut varier selon la réponse de l'API.
    """

    if isinstance(data, dict):
        # Cas classique : les streams sont directement dans le dict
        streams = {}

        for key, value in data.items():
            if key.startswith("stream_"):
                streams[key] = value

        if streams:
            return streams

        # Recherche récursive
        for value in data.values():
            result = flatten_streams(value)

            if result:
                return result

    elif isinstance(data, list):
        streams = {}

        for item in data:
            if isinstance(item, dict):
                for key, value in item.items():
                    if key.startswith("stream_"):
                        streams[key] = value

        if streams:
            return streams

    return {}


def clean_numbers(values):
    """
    Nettoie une série numérique.
    """

    if not isinstance(values, list):
        return []

    cleaned = []

    for value in values:
        try:
            number = float(value)

            if math.isfinite(number):
                cleaned.append(number)

        except (TypeError, ValueError):
            continue

    return cleaned


def average(values):
    values = clean_numbers(values)

    if not values:
        return None

    return round(mean(values), 1)


def maximum(values):
    values = clean_numbers(values)

    if not values:
        return None

    return round(max(values), 1)


def minimum(values):
    values = clean_numbers(values)

    if not values:
        return None

    return round(min(values), 1)


def percentile(values, percentage):
    """
    Petit calcul de percentile sans dépendance externe.
    """

    values = sorted(clean_numbers(values))

    if not values:
        return None

    index = (len(values) - 1) * percentage
    lower = math.floor(index)
    upper = math.ceil(index)

    if lower == upper:
        return round(values[lower], 1)

    result = (
        values[lower]
        + (values[upper] - values[lower])
        * (index - lower)
    )

    return round(result, 1)


def calculate_power_metrics(power):
    power = clean_numbers(power)

    if not power:
        return {}

    result = {
        "samples": len(power),
        "average_watts": average(power),
        "max_watts": maximum(power),
        "min_watts": minimum(power),
        "p95_watts": percentile(power, 0.95),
    }

    # Puissance normalisée approximative :
    # calcul sur fenêtres de 30 secondes.
    #
    # IMPORTANT :
    # ceci suppose que les échantillons sont proches de 1 seconde.
    # Nous pourrons ensuite utiliser le timestamp réel des streams
    # si Nolio nous le fournit.
    if len(power) >= 30:
        rolling = []

        for i in range(29, len(power)):
            window = power[i - 29:i + 1]
            rolling.append(mean(window))

        fourth_power_mean = mean(
            value ** 4 for value in rolling
        )

        normalized_power = fourth_power_mean ** 0.25

        result["normalized_power_approx"] = round(
            normalized_power,
            1
        )

    return result


def calculate_hr_metrics(heart_rate):
    heart_rate = clean_numbers(heart_rate)

    if not heart_rate:
        return {}

    return {
        "samples": len(heart_rate),
        "average_bpm": average(heart_rate),
        "max_bpm": maximum(heart_rate),
        "min_bpm": minimum(heart_rate),
        "p95_bpm": percentile(heart_rate, 0.95),
    }


def calculate_power_zones(power, ftp=310):
    """
    Répartition approximative du temps dans les zones Coggan.

    Z1 < 55%
    Z2 55-75%
    Z3 75-90%
    Z4 90-105%
    Z5 105-120%
    Z6 120-150%
    Z7 > 150%
    """

    power = clean_numbers(power)

    if not power or not ftp:
        return {}

    zones = {
        "Z1_recuperation": 0,
        "Z2_endurance": 0,
        "Z3_tempo": 0,
        "Z4_seuil": 0,
        "Z5_vo2max": 0,
        "Z6_anaerobie": 0,
        "Z7_neuromusculaire": 0,
    }

    for watts in power:
        ratio = watts / ftp

        if ratio < 0.55:
            zones["Z1_recuperation"] += 1
        elif ratio < 0.75:
            zones["Z2_endurance"] += 1
        elif ratio < 0.90:
            zones["Z3_tempo"] += 1
        elif ratio < 1.05:
            zones["Z4_seuil"] += 1
        elif ratio < 1.20:
            zones["Z5_vo2max"] += 1
        elif ratio < 1.50:
            zones["Z6_anaerobie"] += 1
        else:
            zones["Z7_neuromusculaire"] += 1

    return zones


def calculate_hr_drift(power, heart_rate):
    """
    Compare le ratio puissance / FC entre le début et la fin
    de la séance.

    C'est une estimation simple qui sera améliorée lorsque nous
    utiliserons précisément les timestamps.
    """

    power = clean_numbers(power)
    heart_rate = clean_numbers(heart_rate)

    length = min(len(power), len(heart_rate))

    if length < 60:
        return None

    power = power[:length]
    heart_rate = heart_rate[:length]

    half = length // 2

    first_power = mean(power[:half])
    first_hr = mean(heart_rate[:half])

    second_power = mean(power[half:])
    second_hr = mean(heart_rate[half:])

    if first_power <= 0 or second_power <= 0:
        return None

    first_ratio = first_power / first_hr
    second_ratio = second_power / second_hr

    drift = ((second_ratio / first_ratio) - 1) * 100

    return round(drift, 1)


def generate_analysis(training, streams):
    """
    Génère l'analyse de la séance.
    """

    power = streams.get("stream_power", [])
    heart_rate = streams.get("stream_heart_rate", [])

    power = clean_numbers(power)
    heart_rate = clean_numbers(heart_rate)

    analysis = {
        "available_streams": list(streams.keys()),
        "power": calculate_power_metrics(power),
        "heart_rate": calculate_hr_metrics(heart_rate),
        "power_zones": calculate_power_zones(power),
        "heart_rate_drift_percent": calculate_hr_drift(
            power,
            heart_rate
        ),
    }

    # --------------------------------------------------------
    # Informations provenant directement de la séance
    # --------------------------------------------------------

    if isinstance(training, dict):
        interesting_fields = [
            "id",
            "name",
            "date",
            "start_date",
            "duration",
            "distance",
            "average_power",
            "normalized_power",
            "ftp",
            "max_heart_rate",
            "rpe",
            "training_load",
            "load_coggan",
            "load_foster",
        ]

        summary = {}

        for field in interesting_fields:
            if field in training:
                summary[field] = training[field]

        analysis["training"] = summary

    # --------------------------------------------------------
    # Analyse textuelle simple
    # --------------------------------------------------------

    observations = []

    power_metrics = analysis["power"]
    hr_metrics = analysis["heart_rate"]

    if power_metrics.get("average_watts") is not None:
        observations.append(
            f"Puissance moyenne : "
            f"{power_metrics['average_watts']} W."
        )

    if power_metrics.get("max_watts") is not None:
        observations.append(
            f"Puissance maximale observée : "
            f"{power_metrics['max_watts']} W."
        )

    if power_metrics.get("normalized_power_approx") is not None:
        observations.append(
            f"Puissance normalisée approximative : "
            f"{power_metrics['normalized_power_approx']} W."
        )

    if hr_metrics.get("average_bpm") is not None:
        observations.append(
            f"FC moyenne : "
            f"{hr_metrics['average_bpm']} bpm."
        )

    if hr_metrics.get("max_bpm") is not None:
        observations.append(
            f"FC maximale observée : "
            f"{hr_metrics['max_bpm']} bpm."
        )

    drift = analysis["heart_rate_drift_percent"]

    if drift is not None:
        if drift > 5:
            observations.append(
                f"Dérive cardiaque estimée importante : "
                f"{drift} %."
            )
        elif drift > 2:
            observations.append(
                f"Dérive cardiaque estimée modérée : "
                f"{drift} %."
            )
        else:
            observations.append(
                f"Dérive cardiaque estimée faible : "
                f"{drift} %."
            )

    analysis["observations"] = observations

    return analysis


# ============================================================
# ANALYSE DE LA DERNIÈRE SÉANCE
# ============================================================

@app.route("/analyse/derniere-seance")
def analyse_derniere_seance():

    # 1. Récupérer les séances
    data, error = nolio_get(
        "/api/get/training/",
        params={"limit": 20}
    )

    if error:
        return error

    # --------------------------------------------------------
    # Trouver la liste des séances
    # --------------------------------------------------------

    trainings = []

    if isinstance(data, list):
        trainings = data

    elif isinstance(data, dict):

        for key in ["results", "data", "trainings"]:
            if isinstance(data.get(key), list):
                trainings = data[key]
                break

    if not trainings:
        return jsonify({
            "error": "Aucune séance trouvée.",
            "raw_training_response": data
        }), 404

    # --------------------------------------------------------
    # Prendre la première séance.
    #
    # On vérifiera ensuite l'ordre exact renvoyé par Nolio.
    # --------------------------------------------------------

    training = trainings[0]

    if not isinstance(training, dict):
        return jsonify({
            "error": "Format de séance inattendu.",
            "training": training
        }), 500

    training_id = training.get("id")

    if not training_id:
        return jsonify({
            "error": "Impossible de trouver l'identifiant de la séance.",
            "training": training
        }), 500

    # --------------------------------------------------------
    # 2. Récupérer les streams
    # --------------------------------------------------------

    stream_data, error = nolio_get(
        "/api/get/training/streams/",
        params={"id": training_id}
    )

    if error:
        return error

    streams = flatten_streams(stream_data)

    if not streams:
        return jsonify({
            "error": "Aucun stream exploitable trouvé.",
            "training": training,
            "raw_stream_response": stream_data
        }), 404

    # --------------------------------------------------------
    # 3. Calculer l'analyse
    # --------------------------------------------------------

    analysis = generate_analysis(
        training,
        streams
    )

    # --------------------------------------------------------
    # 4. Retourner le résultat
    # --------------------------------------------------------

    return jsonify(analysis)


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000))
