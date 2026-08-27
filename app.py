import os
import secrets
import base64
import requests
import math
from statistics import mean

from flask import Flask, redirect, request, session, jsonify


# ============================================================
# CONFIGURATION
# ============================================================

app = Flask(__name__)

app.secret_key = os.environ["FLASK_SECRET_KEY"]

BASE_URL = "https://www.nolio.io"

CLIENT_ID = "5BXkbpFyUlgR3U6Hoiw7hK9y1E0qRelTwpnl55rZ"

CLIENT_SECRET = os.environ["NOLIO_CLIENT_SECRET"]

REDIRECT_URI = os.environ["NOLIO_REDIRECT_URI"]


# ============================================================
# PAGE D'ACCUEIL
# ============================================================

@app.route("/")
def home():
    return """
    <h1>Nolio ↔ ChatGPT</h1>

    <p>
        <a href="/login">
            Connecter mon compte Nolio
        </a>
    </p>

    <p>
        <a href="/user">
            Voir mon profil Nolio
        </a>
    </p>

    <p>
        <a href="/sessions">
            Voir mes séances
        </a>
    </p>

    <p>
        <a href="/analyse/derniere-seance">
            Analyser ma dernière séance
        </a>
    </p>
    """


# ============================================================
# OAUTH - LOGIN
# ============================================================

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


# ============================================================
# OAUTH - CALLBACK
# ============================================================

@app.route("/callback")
def callback():

    received_state = request.args.get("state")

    saved_state = session.get("oauth_state")

    if received_state != saved_state:
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

    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")

    if not access_token:
        return jsonify({
            "error": "Aucun access_token reçu.",
            "response": tokens
        }), 500

    session["access_token"] = access_token

    if refresh_token:
        session["refresh_token"] = refresh_token

    session.pop("oauth_state", None)

    return redirect("/")


# ============================================================
# REFRESH ACCESS TOKEN
# ============================================================

def refresh_access_token():

    refresh_token = session.get("refresh_token")

    if not refresh_token:
        return False

    credentials = f"{CLIENT_ID}:{CLIENT_SECRET}".encode()

    basic_auth = base64.b64encode(credentials).decode()

    response = requests.post(
        f"{BASE_URL}/api/token/",
        headers={
            "Authorization": f"Basic {basic_auth}"
        },
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=30,
    )

    if response.status_code != 200:
        session.pop("access_token", None)
        session.pop("refresh_token", None)

        return False

    tokens = response.json()

    access_token = tokens.get("access_token")
    new_refresh_token = tokens.get("refresh_token")

    if not access_token:
        session.pop("access_token", None)
        return False

    session["access_token"] = access_token

    # Certains systèmes OAuth renvoient un nouveau refresh token.
    # S'il n'est pas renvoyé, on conserve l'ancien.
    if new_refresh_token:
        session["refresh_token"] = new_refresh_token

    return True


# ============================================================
# REQUÊTES NOLIO
# ============================================================

def nolio_get(path, params=None):
    """
    Effectue une requête GET vers Nolio.

    Si l'access_token est expiré :
    1. on utilise le refresh_token ;
    2. on récupère un nouveau access_token ;
    3. on rejoue automatiquement la requête.
    """

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

    # --------------------------------------------------------
    # Access token expiré
    # --------------------------------------------------------

    if response.status_code == 401:

        refreshed = refresh_access_token()

        if not refreshed:
            return None, redirect("/login")

        access_token = session.get("access_token")

        response = requests.get(
            f"{BASE_URL}{path}",
            headers={
                "Authorization": f"Bearer {access_token}"
            },
            params=params,
            timeout=30,
        )

    response.raise_for_status()

    return response.json(), None


# ============================================================
# PROFIL UTILISATEUR
# ============================================================

@app.route("/user")
def user():

    data, error = nolio_get(
        "/api/get/user/"
    )

    if error:
        return error

    return jsonify(data)


# ============================================================
# LISTE DES SÉANCES
# ============================================================

@app.route("/sessions")
def sessions():

    data, error = nolio_get(
        "/api/get/training/",
        params={
            "limit": 20
        }
    )

    if error:
        return error

    return jsonify(data)


# ============================================================
# STREAMS D'UNE SÉANCE
# ============================================================

@app.route("/session/<int:training_id>/streams")
def session_streams(training_id):

    data, error = nolio_get(
        "/api/get/training/streams/",
        params={
            "id": training_id
        }
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
    Nolio en dictionnaire de streams.

    Exemple :

    {
        "stream_watts": [...],
        "stream_heartrate": [...],
        "stream_cadence": [...]
    }
    """

    if isinstance(data, dict):

        streams = {}

        # ----------------------------------------------------
        # Les streams sont directement dans le dictionnaire
        # ----------------------------------------------------

        for key, value in data.items():

            if key.startswith("stream_"):
                streams[key] = value

        if streams:
            return streams

        # ----------------------------------------------------
        # Recherche récursive
        # ----------------------------------------------------

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


# ============================================================
# NETTOYAGE DES DONNÉES
# ============================================================

def clean_numbers(values):
    """
    Transforme une série en liste de nombres valides.
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


# ============================================================
# MOYENNE
# ============================================================

def average(values):

    values = clean_numbers(values)

    if not values:
        return None

    return round(mean(values), 1)


# ============================================================
# MAXIMUM
# ============================================================

def maximum(values):

    values = clean_numbers(values)

    if not values:
        return None

    return round(max(values), 1)


# ============================================================
# MINIMUM
# ============================================================

def minimum(values):

    values = clean_numbers(values)

    if not values:
        return None

    return round(min(values), 1)


# ============================================================
# PERCENTILE
# ============================================================

def percentile(values, percentage):
    """
    Calcul simple d'un percentile.

    percentage :
        0.95 = 95e percentile
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
        +
        (values[upper] - values[lower])
        *
        (index - lower)
    )

    return round(result, 1)


# ============================================================
# MÉTRIQUES PUISSANCE
# ============================================================

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

    # --------------------------------------------------------
    # Puissance normalisée approximative
    # --------------------------------------------------------
    #
    # Hypothèse :
    # les données sont proches de 1 échantillon / seconde.
    #
    # Ce n'est PAS une NP officielle.
    # --------------------------------------------------------

    if len(power) >= 30:

        rolling = []

        for i in range(29, len(power)):

            window = power[i - 29:i + 1]

            rolling_average = mean(window)

            rolling.append(rolling_average)

        if rolling:

            fourth_power_mean = mean(
                value ** 4
                for value in rolling
            )

            normalized_power = fourth_power_mean ** 0.25

            result["normalized_power_approx"] = round(
                normalized_power,
                1
            )

    return result


# ============================================================
# MÉTRIQUES FRÉQUENCE CARDIAQUE
# ============================================================

def calculate_hr_metrics(heart_rate):

    heart_rate = clean_numbers(heart_rate)

    if not heart_rate:
        return {}

    return {
        "samples": len(heart_rate),
        "average_bpm": average(heart_rate),
        "max_bpm": maximum(heart_rate),
        "min_bpm": minimum(heart_rate),
        "p95_bpm": percentile(
            heart_rate,
            0.95
        ),
    }


# ============================================================
# ZONES DE PUISSANCE
# ============================================================

def calculate_power_zones(power, ftp=310):
    """
    Répartition approximative des échantillons
    dans les zones de puissance.

    Z1 : < 55 %
    Z2 : 55 - 75 %
    Z3 : 75 - 90 %
    Z4 : 90 - 105 %
    Z5 : 105 - 120 %
    Z6 : 120 - 150 %
    Z7 : > 150 %
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


# ============================================================
# DÉRIVE CARDIAQUE
# ============================================================

def calculate_hr_drift(power, heart_rate):
    """
    Estimation simple de la dérive cardiaque.

    On compare le ratio puissance / FC entre :
    - la première moitié ;
    - la seconde moitié.

    Formule :

        ratio = puissance moyenne / FC moyenne

        dérive = ((ratio_2 / ratio_1) - 1) * 100

    Cette méthode reste approximative.
    """

    power = clean_numbers(power)

    heart_rate = clean_numbers(heart_rate)

    length = min(
        len(power),
        len(heart_rate)
    )

    if length < 60:
        return None

    power = power[:length]

    heart_rate = heart_rate[:length]

    half = length // 2

    first_power = mean(
        power[:half]
    )

    first_hr = mean(
        heart_rate[:half]
    )

    second_power = mean(
        power[half:]
    )

    second_hr = mean(
        heart_rate[half:]
    )

    if (
        first_power <= 0
        or second_power <= 0
        or first_hr <= 0
        or second_hr <= 0
    ):
        return None

    first_ratio = (
        first_power / first_hr
    )

    second_ratio = (
        second_power / second_hr
    )

    drift = (
        (second_ratio / first_ratio)
        - 1
    ) * 100

    return round(drift, 1)


# ============================================================
# ANALYSE COMPLÈTE
# ============================================================

def generate_analysis(training, streams):
    """
    Génère l'analyse complète de la séance.
    """

    # --------------------------------------------------------
    # Récupération des streams
    # --------------------------------------------------------

    power = streams.get(
        "stream_watts",
        []
    )

    heart_rate = streams.get(
        "stream_heartrate",
        []
    )

    cadence = streams.get(
        "stream_cadence",
        []
    )

    altitude = streams.get(
        "stream_altitude",
        []
    )

    distance_stream = streams.get(
        "stream_distance",
        []
    )

    time_stream = streams.get(
        "stream_time",
        []
    )

    # --------------------------------------------------------
    # Nettoyage
    # --------------------------------------------------------

    power = clean_numbers(power)

    heart_rate = clean_numbers(
        heart_rate
    )

    cadence = clean_numbers(
        cadence
    )

    altitude = clean_numbers(
        altitude
    )

    distance_stream = clean_numbers(
        distance_stream
    )

    time_stream = clean_numbers(
        time_stream
    )

    # --------------------------------------------------------
    # Calcul des métriques
    # --------------------------------------------------------

    power_metrics = calculate_power_metrics(
        power
    )

    hr_metrics = calculate_hr_metrics(
        heart_rate
    )

    power_zones = calculate_power_zones(
        power
    )

    hr_drift = calculate_hr_drift(
        power,
        heart_rate
    )

    # --------------------------------------------------------
    # Objet principal
    # --------------------------------------------------------

    analysis = {
        "available_streams": list(
            streams.keys()
        ),

        "power": power_metrics,

        "heart_rate": hr_metrics,

        "power_zones": power_zones,

        "heart_rate_drift_percent": hr_drift,
    }

    # --------------------------------------------------------
    # Métriques supplémentaires
    # --------------------------------------------------------

    additional_metrics = {}

    if cadence:

        additional_metrics["cadence"] = {
            "average_rpm": average(
                cadence
            ),
            "max_rpm": maximum(
                cadence
            ),
            "min_rpm": minimum(
                cadence
            ),
            "samples": len(cadence),
        }

    if altitude:

        additional_metrics["altitude"] = {
            "min_m": minimum(
                altitude
            ),
            "max_m": maximum(
                altitude
            ),
        }

    if distance_stream:

        additional_metrics[
            "distance_stream"
        ] = {
            "start": round(
                distance_stream[0],
                3
            ),
            "end": round(
                distance_stream[-1],
                3
            ),
        }

    if time_stream:

        additional_metrics[
            "stream_time"
        ] = {
            "start": round(
                time_stream[0],
                2
            ),
            "end": round(
                time_stream[-1],
                2
            ),
            "duration_seconds": round(
                time_stream[-1]
                - time_stream[0],
                2
            ),
        }

    if additional_metrics:

        analysis[
            "additional_metrics"
        ] = additional_metrics

    # ========================================================
    # INFORMATIONS DE LA SÉANCE
    # ========================================================

    if isinstance(training, dict):

        interesting_fields = [
            "id",
            "nolio_id",
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

        analysis[
            "training"
        ] = summary

    # ========================================================
    # OBSERVATIONS
    # ========================================================

    observations = []

    # --------------------------------------------------------
    # Puissance moyenne
    # --------------------------------------------------------

    if power_metrics.get(
        "average_watts"
    ) is not None:

        observations.append(
            f"Puissance moyenne : "
            f"{power_metrics['average_watts']} W."
        )

    # --------------------------------------------------------
    # Puissance maximale
    # --------------------------------------------------------

    if power_metrics.get(
        "max_watts"
    ) is not None:

        observations.append(
            f"Puissance maximale observée : "
            f"{power_metrics['max_watts']} W."
        )

    # --------------------------------------------------------
    # Puissance normalisée
    # --------------------------------------------------------

    if power_metrics.get(
        "normalized_power_approx"
    ) is not None:

        observations.append(
            f"Puissance normalisée approximative : "
            f"{power_metrics['normalized_power_approx']} W."
        )

    # --------------------------------------------------------
    # FC moyenne
    # --------------------------------------------------------

    if hr_metrics.get(
        "average_bpm"
    ) is not None:

        observations.append(
            f"FC moyenne : "
            f"{hr_metrics['average_bpm']} bpm."
        )

    # --------------------------------------------------------
    # FC maximale
    # --------------------------------------------------------

    if hr_metrics.get(
        "max_bpm"
    ) is not None:

        observations.append(
            f"FC maximale observée : "
            f"{hr_metrics['max_bpm']} bpm."
        )

    # --------------------------------------------------------
    # Dérive cardiaque
    # --------------------------------------------------------

    if hr_drift is not None:

        if hr_drift > 5:

            observations.append(
                f"Dérive cardiaque estimée importante : "
                f"{hr_drift} %."
            )

        elif hr_drift > 2:

            observations.append(
                f"Dérive cardiaque estimée modérée : "
                f"{hr_drift} %."
            )

        elif hr_drift < -2:

            observations.append(
                f"Dérive cardiaque négative estimée : "
                f"{hr_drift} %."
            )

        else:

            observations.append(
                f"Dérive cardiaque estimée faible : "
                f"{hr_drift} %."
            )

    analysis[
        "observations"
    ] = observations

    return analysis


# ============================================================
# ANALYSE DE LA DERNIÈRE SÉANCE
# ============================================================

@app.route("/analyse/derniere-seance")
def analyse_derniere_seance():

    # --------------------------------------------------------
    # Récupération des séances
    # --------------------------------------------------------

    data, error = nolio_get(
        "/api/get/training/",
        params={
            "limit": 20
        }
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

        for key in [
            "results",
            "data",
            "trainings"
        ]:

            if isinstance(
                data.get(key),
                list
            ):

                trainings = data[key]

                break

    # --------------------------------------------------------
    # Aucune séance
    # --------------------------------------------------------

    if not trainings:

        return jsonify({
            "error": "Aucune séance trouvée.",
            "raw_training_response": data
        }), 404

    # --------------------------------------------------------
    # Première séance
    # --------------------------------------------------------

    training = trainings[0]

    if not isinstance(
        training,
        dict
    ):

        return jsonify({
            "error": "Format de séance inattendu.",
            "training": training
        }), 500

    # --------------------------------------------------------
    # ID de la séance
    # --------------------------------------------------------

    training_id = training.get(
        "nolio_id"
    )

    if not training_id:

        training_id = training.get(
            "id"
        )

    if not training_id:

        return jsonify({
            "error": (
                "Impossible de trouver "
                "l'identifiant de la séance."
            ),
            "training": training
        }), 500

    # --------------------------------------------------------
    # Récupération des streams
    # --------------------------------------------------------

    stream_data, error = nolio_get(
        "/api/get/training/streams/",
        params={
            "id": training_id
        }
    )

    if error:
        return error

    # --------------------------------------------------------
    # Transformation des streams
    # --------------------------------------------------------

    streams = flatten_streams(
        stream_data
    )

    if not streams:

        return jsonify({
            "error": (
                "Aucun stream exploitable trouvé."
            ),
            "training": training,
            "raw_stream_response": stream_data
        }), 404

    # --------------------------------------------------------
    # Analyse
    # --------------------------------------------------------

    analysis = generate_analysis(
        training,
        streams
    )

    return jsonify(
        analysis
    )


# ============================================================
# LANCEMENT LOCAL
# ============================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=int(
            os.environ.get(
                "PORT",
                10000
            )
        )
    )
