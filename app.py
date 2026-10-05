import os
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode

import psycopg
import requests
from flask import Flask, jsonify, redirect, request

app = Flask(__name__)

ML_API = "https://api.mercadolibre.com"


# =========================================================
# BANCO DE DADOS
# =========================================================

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
                    (
                        user_id,
                        access_token,
                        refresh_token,
                        expires_at,
                        updated_at
                    )
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


def get_token_record():
    init_db()

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    user_id,
                    access_token,
                    refresh_token,
                    expires_at
                FROM ml_tokens
                ORDER BY updated_at DESC
                LIMIT 1
            """)

            return cur.fetchone()


# =========================================================
# TOKEN AUTOMÁTICO
# =========================================================

def get_valid_token():
    row = get_token_record()

    if not row:
        return None

    user_id, access_token, refresh_token, expires_at = row

    now = datetime.now(timezone.utc)

    # Se ainda estiver válido por mais de 5 minutos, usa o atual
    if expires_at and expires_at > now + timedelta(minutes=5):
        return access_token

    # Se não houver refresh token, não dá para renovar
    if not refresh_token:
        return access_token

    payload = {
        "grant_type": "refresh_token",
        "client_id": os.environ.get("ML_CLIENT_ID"),
        "client_secret": os.environ.get("ML_CLIENT_SECRET"),
        "refresh_token": refresh_token
    }

    try:
        r = requests.post(
            f"{ML_API}/oauth/token",
            data=payload,
            timeout=20
        )
    except requests.RequestException:
        return None

    if not r.ok:
        return None

    data = r.json()

    new_access_token = data.get("access_token")
    new_refresh_token = data.get("refresh_token") or refresh_token
    expires_in = int(data.get("expires_in") or 0)

    if not new_access_token:
        return None

    new_expires_at = (
        datetime.now(timezone.utc)
        + timedelta(seconds=expires_in)
    )

    save_tokens(
        user_id,
        new_access_token,
        new_refresh_token,
        new_expires_at
    )

    return new_access_token


# =========================================================
# ROTAS BÁSICAS
# =========================================================

@app.get("/")
def home():
    return jsonify({
        "app": "MV Oferta Certa",
        "status": "online",
        "robo": "pronto",
        "mercado_livre": "integrado"
    })


@app.get("/health")
def health():
    return jsonify({
        "ok": True,
        "app": "MV Oferta Certa"
    })


# =========================================================
# LOGIN MERCADO LIVRE
# =========================================================

@app.get("/login")
def login():
    client_id = os.environ.get("ML_CLIENT_ID")
    redirect_uri = os.environ.get("ML_REDIRECT_URI")

    if not client_id or not redirect_uri:
        return jsonify({
            "ok": False,
            "erro": "ML_CLIENT_ID ou ML_REDIRECT_URI não configurado"
        }), 500

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri
    }

    url = (
        "https://auth.mercadolivre.com.br/authorization?"
        + urlencode(params)
    )

    return redirect(url)


# =========================================================
# CALLBACK OAUTH
# =========================================================

@app.get("/callback")
def callback():
    code = request.args.get("code")

    if not code:
        return jsonify({
            "ok": False,
            "erro": "Código de autorização não recebido"
        }), 400

    payload = {
        "grant_type": "authorization_code",
        "client_id": os.environ.get("ML_CLIENT_ID"),
        "client_secret": os.environ.get("ML_CLIENT_SECRET"),
        "code": code,
        "redirect_uri": os.environ.get("ML_REDIRECT_URI")
    }

    try:
        r = requests.post(
            f"{ML_API}/oauth/token",
            data=payload,
            timeout=20
        )
    except requests.RequestException as e:
        return jsonify({
            "ok": False,
            "erro": "Falha de comunicação com Mercado Livre",
            "detalhe": str(e)
        }), 502

    try:
        data = r.json()
    except ValueError:
        data = {
            "resposta": r.text
        }

    if not r.ok:
        return jsonify({
            "ok": False,
            "erro": "Falha na autorização do Mercado Livre",
            "detalhe_ml": data
        }), r.status_code

    user_id = data.get("user_id")
    access_token = data.get("access_token")
    refresh_token = data.get("refresh_token")
    expires_in = int(data.get("expires_in") or 0)

    if not user_id or not access_token:
        return jsonify({
            "ok": False,
            "erro": "Resposta OAuth incompleta"
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


# =========================================================
# TESTE DA CONEXÃO
# =========================================================

@app.get("/teste-ml")
def teste_ml():
    access_token = get_valid_token()

    if not access_token:
        return jsonify({
            "ok": False,
            "erro": "Nenhum token válido encontrado"
        }), 401

    try:
        r = requests.get(
            f"{ML_API}/users/me",
            headers={
                "Authorization": f"Bearer {access_token}"
            },
            timeout=20
        )
    except requests.RequestException as e:
        return jsonify({
            "ok": False,
            "erro": "Falha de comunicação com Mercado Livre",
            "detalhe": str(e)
        }), 502

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


# =========================================================
# BUSCA DE PRODUTOS
# =========================================================

@app.get("/buscar-ofertas")
def buscar_ofertas():
    termo = request.args.get("q", "smart tv").strip()

    if not termo:
        termo = "smart tv"

    access_token = get_valid_token()

    if not access_token:
        return jsonify({
            "ok": False,
            "erro": "Mercado Livre não conectado"
        }), 401

    params = {
        "site_id": "MLB",
        "status": "active",
        "q": termo
    }

    try:
        r = requests.get(
            f"{ML_API}/products/search",
            params=params,
            headers={
                "Authorization": f"Bearer {access_token}"
            },
            timeout=20
        )
    except requests.RequestException as e:
        return jsonify({
            "ok": False,
            "erro": "Falha de comunicação com Mercado Livre",
            "detalhe": str(e)
        }), 502

    try:
        data = r.json()
    except ValueError:
        data = {
            "resposta": r.text
        }

    if not r.ok:
        return jsonify({
            "ok": False,
            "status_ml": r.status_code,
            "erro": "Falha ao buscar produtos",
            "detalhe_ml": data
        }), r.status_code

    produtos = []

    for item in data.get("results", []):
        produtos.append({
            "id": item.get("id"),
            "nome": item.get("name"),
            "status": item.get("status"),
            "dominio": item.get("domain_id")
        })

    return jsonify({
        "ok": True,
        "busca": termo,
        "quantidade": len(produtos),
        "produtos": produtos
    })


# =========================================================
# ESTRUTURA DO ROBÔ
# =========================================================

@app.get("/ofertas")
def ofertas():
    return jsonify({
        "app": "MV Oferta Certa",
        "status": "estrutura do robô pronta",
        "criterios": [
            "desconto",
            "preco",
            "frete",
            "relevancia"
        ],
        "proximo_passo": "ranquear ofertas e integrar afiliados"
    })
