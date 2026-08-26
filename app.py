import os
import secrets
import base64
import requests
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

    return """
    <h1>Nolio connecté ✅</h1>
    <p>La connexion à Nolio fonctionne.</p>
    <p><a href="/user">Tester l'accès à mon compte</a></p>
    <p><a href="/athletes">Voir mes athlètes</a></p>
    """


@app.route("/user")
def user():
    access_token = session.get("access_token")

    if not access_token:
        return redirect("/login")

    response = requests.get(
        f"{BASE_URL}/api/get/user/",
        headers={
            "Authorization": f"Bearer {access_token}"
        },
        timeout=30,
    )

    if response.status_code == 401:
        return "Le token a expiré. Il faudra utiliser le refresh token.", 401

    response.raise_for_status()

    return jsonify(response.json())


# --- Helper générique pour appeler l'API Nolio avec le token en session ---

def nolio_get(path, params=None):
    access_token = session.get("access_token")
    if not access_token:
        return None, redirect("/login")

    response = requests.get(
        f"{BASE_URL}{path}",
        headers={"Authorization": f"Bearer {access_token}"},
        params=params,
        timeout=30,
    )
    if response.status_code == 401:
        return None, ("Le token a expiré. Il faudra utiliser le refresh token.", 401)

    response.raise_for_status()
    return response.json(), None


# --- Liste des athlètes du coach connecté ---

@app.route("/athletes")
def athletes():
    data, error = nolio_get("/api/get/athletes/", params={"limit": 300})
    if error:
        return error
    return jsonify(data)


# --- Séances d'un athlète (filtrable par ?from=YYYY-MM-DD&to=YYYY-MM-DD) ---

@app.route("/athlete/<int:athlete_id>/sessions")
def athlete_sessions(athlete_id):
    params = {"athlete_id": athlete_id, "limit": 300}
    if request.args.get("from"):
        params["from"] = request.args["from"]
    if request.args.get("to"):
        params["to"] = request.args["to"]

    data, error = nolio_get("/api/get/training/", params=params)
    if error:
        return error
    return jsonify(data)


# --- Analyse simple : volume, distance, dénivelé, RPE moyen ---

@app.route("/athlete/<int:athlete_id>/analysis")
def athlete_analysis(athlete_id):
    params = {"athlete_id": athlete_id, "limit": 300}
    if request.args.get("from"):
        params["from"] = request.args["from"]
    if request.args.get("to"):
        params["to"] = request.args["to"]

    data, error = nolio_get("/api/get/training/", params=params)
    if error:
        return error

    items = data if isinstance(data, list) else data.get("results", data)

    total_duration = sum(s.get("duration", 0) for s in items)
    total_distance = sum(s.get("distance", 0) for s in items)
    total_elevation = sum(s.get("elevation_gain", 0) for s in items)
    rpes = [s["rpe"] for s in items if s.get("rpe") is not None]
    avg_rpe = round(sum(rpes) / len(rpes), 2) if rpes else None

    return jsonify({
        "athlete_id": athlete_id,
        "nb_sessions": len(items),
        "total_duration_s": total_duration,
        "total_distance_m": total_distance,
        "total_elevation_gain_m": total_elevation,
        "avg_rpe": avg_rpe,
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
