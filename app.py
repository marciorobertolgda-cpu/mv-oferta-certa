import os
from datetime import datetime, timezone, timedelta

import psycopg
import requests
from flask import Flask, jsonify, redirect, request

app = Flask(__name__)

ML_API = "https://api.mercadolibre.com"


def get_conn():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL não configurada")
    return psycopg.connect(database_url)


def init_db():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS ml_tokens (
                    user_id BIGINT PRIMARY KEY,
                    access_token TEXT NOT NULL,
                    refresh_token TEXT,
                    expires_at TIMESTAMPTZ,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)


def save_tokens(user_id, access_token, refresh_token, expires_at):
    init_db()

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO ml_tokens
                    (user_id, access_token, refresh_token, expires_at, updated_at)
                VALUES (%s, %s, %s, %s, NOW())
                ON CONFLICT (user_id)
                DO UPDATE SET
                    access_token = EXCLUDED.access_token,
                    refresh_token = COALESCE(
                        EXCLUDED.refresh_token,
                        ml_tokens.refresh_token
                    ),
                    expires_at = EXCLUDED.expires_at,
                    updated_at = NOW()
            """, (
                user_id,
                access_token,
                refresh_token,
                expires_at
            ))


@app.get("/")
def home():
    return jsonify({
        "app": "MV Oferta Certa",
        "status": "online",
        "mercado_livre": "pronto para conectar"
    })


@app.get("/health")
def health():
    return jsonify({"ok": True})


@app.get("/login")
def login():
    client_id = os.environ.get("ML_CLIENT_ID")
    redirect_uri = os.environ.get("ML_REDIRECT_URI")

    if not client_id or not redirect_uri:
        return jsonify({
            "error": "Configure ML_CLIENT_ID e ML_REDIRECT_URI no Render"
        }), 500

    url = (
        "https://auth.mercadolivre.com.br/authorization"
        f"?response_type=code&client_id={client_id}"
        f"&redirect_uri={redirect_uri}"
    )

    return redirect(url)


@app.get("/callback")
def callback():
    code = request.args.get("code")

    if not code:
        return jsonify({
            "error": "Código de autorização não recebido"
        }), 400

    payload = {
        "grant_type": "authorization_code",
        "client_id": os.environ.get("ML_CLIENT_ID"),
        "client_secret": os.environ.get("ML_CLIENT_SECRET"),
        "code": code,
        "redirect_uri": os.environ.get("ML_REDIRECT_URI"),
    }

    r = requests.post(
        f"{ML_API}/oauth/token",
        data=payload,
        timeout=20
    )

    data = r.json()

    if not r.ok:
        return jsonify(data), r.status_code

    user_id = data.get("user_id")
    access_token = data.get("access_token")
    refresh_token = data.get("refresh_token")
    expires_in = int(data.get("expires_in") or 0)

    if not user_id or not access_token:
        return jsonify({
            "error": "Resposta OAuth incompleta"
        }), 502

    expires_at = (
        datetime.now(timezone.utc)
        + timedelta(seconds=expires_in)
    )

    save_tokens(
        user_id,
        access_token,
        refresh_token,
        expires_at
    )

    return jsonify({
        "conectado": True,
        "mensagem": "Mercado Livre autorizado e token armazenado."
    })


@app.get("/ofertas")
def ofertas():
    return jsonify({
        "status": "estrutura pronta",
        "proximo_passo": "coletar e ranquear ofertas",
        "criterios": [
            "desconto",
            "preco",
            "frete",
            "relevancia"
        ]
    })
def get_saved_token():
    init_db()

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT access_token
                FROM ml_tokens
                ORDER BY updated_at DESC
                LIMIT 1
            """)
            row = cur.fetchone()

    return row[0] if row else None


@app.get("/teste-ml")
def teste_ml():
    access_token = get_saved_token()

    if not access_token:
        return jsonify({
            "ok": False,
            "erro": "Nenhum token do Mercado Livre encontrado"
        }), 401

    r = requests.get(
        f"{ML_API}/users/me",
        headers={
            "Authorization": f"Bearer {access_token}"
        },
        timeout=20
    )

    if not r.ok:
        return jsonify({
            "ok": False,
            "status_ml": r.status_code,
            "erro": "Falha ao consultar Mercado Livre"
        }), r.status_code

    data = r.json()

    return jsonify({
        "ok": True,
        "mercado_livre": "conectado",
        "user_id": data.get("id"),
        "nickname": data.get("nickname")
    })
    @app.get("/buscar-ofertas")
    def buscar_ofertas():
    termo = request.args.get("q", "smart tv")
    access_token = get_saved_token()

    if not access_token:
        return jsonify({
            "ok": False,
            "erro": "Mercado Livre não conectado"
        }), 401

    r = requests.get(
        f"{ML_API}/products/search",
        params={
            "site_id": "MLB",
            "status": "active",
            "q": termo,
            "limit": 10
        },
        headers={
            "Authorization": f"Bearer {access_token}"
        },
        timeout=20
    )

    if not r.ok:
        try:
            detalhe_ml = r.json()
        except ValueError:
            detalhe_ml = r.text

        return jsonify({
            "ok": False,
            "status_ml": r.status_code,
            "erro": "Falha ao buscar ofertas",
            "detalhe_ml": detalhe_ml
        }), r.status_code

    data = r.json()

    return jsonify({
        "ok": True,
        "busca": termo,
        "resposta_ml": data
    })   
