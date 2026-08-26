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
    <p><a href="/user">Voir mon profil Nolio</a></p>
    <p><a href="/sessions">Voir mes séances</a></p>
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


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000))
    )
