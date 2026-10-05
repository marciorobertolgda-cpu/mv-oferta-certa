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

@app.get("/detalhes-produtos")
def detalhes_produtos():
    termo = request.args.get("q", "smart tv").strip() or "smart tv"

    access_token = get_valid_token()
    if not access_token:
        return jsonify({"ok": False, "erro": "Mercado Livre não conectado"}), 401

    try:
        busca = requests.get(
            f"{ML_API}/products/search",
            params={"site_id": "MLB", "status": "active", "q": termo},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=20
        )

        if not busca.ok:
            return jsonify({
                "ok": False,
                "erro": "Falha ao buscar produtos",
                "status_ml": busca.status_code
            }), busca.status_code

        resultados = busca.json().get("results", [])[:10]
        produtos = []

        for item in resultados:
            produto = {
                "id": item.get("id"),
                "nome": item.get("name"),
                "status": item.get("status"),
                "dominio": item.get("domain_id")
            }

            produtos.append(produto)

        return jsonify({
            "ok": True,
            "busca": termo,
            "quantidade": len(produtos),
            "produtos": produtos
        })

    except requests.RequestException as e:
        return jsonify({
            "ok": False,
            "erro": "Falha de comunicação com Mercado Livre",
            "detalhe": str(e)
        }), 502
        
@app.get("/produto-detalhes/<produto_id>")
def produto_detalhes(produto_id):
    access_token = get_valid_token()

    if not access_token:
        return jsonify({
            "ok": False,
            "erro": "Mercado Livre não conectado"
        }), 401

    try:
        r = requests.get(
            f"{ML_API}/products/{produto_id}",
            headers={
                "Authorization": f"Bearer {access_token}"
            },
            timeout=20
        )

        try:
            data = r.json()
        except ValueError:
            data = {"resposta": r.text}

        if not r.ok:
            return jsonify({
                "ok": False,
                "status_ml": r.status_code,
                "erro": "Falha ao consultar produto",
                "detalhe_ml": data
            }), r.status_code

        return jsonify({
            "ok": True,
            "produto": data
        })

    except requests.RequestException as e:
        return jsonify({
            "ok": False,
            "erro": "Falha de comunicação com Mercado Livre",
            "detalhe": str(e)
        }), 502

@app.get("/produto-resumo/<produto_id>")
def produto_resumo(produto_id):
    access_token = get_valid_token()

    if not access_token:
        return jsonify({
            "ok": False,
            "erro": "Mercado Livre não conectado"
        }), 401

    try:
        r = requests.get(
            f"{ML_API}/products/{produto_id}",
            headers={
                "Authorization": f"Bearer {access_token}"
            },
            timeout=20
        )

        data = r.json()

        if not r.ok:
            return jsonify({
                "ok": False,
                "erro": "Falha ao consultar produto",
                "detalhe_ml": data
            }), r.status_code

        fotos = []
        for foto in data.get("pictures", []):
            url = foto.get("secure_url") or foto.get("url")
            if url:
                fotos.append(url)

        atributos = {}
        for atributo in data.get("attributes", []):
            nome = atributo.get("name")
            valor = atributo.get("value_name")
            if nome and valor:
                atributos[nome] = valor

        return jsonify({
            "ok": True,
            "id": data.get("id"),
            "nome": data.get("name"),
            "status": data.get("status"),
            "dominio": data.get("domain_id"),
            "foto": fotos[0] if fotos else None,
            "fotos": fotos,
            "atributos": atributos
        })

    except requests.RequestException as e:
        return jsonify({
            "ok": False,
            "erro": "Falha de comunicação com Mercado Livre",
            "detalhe": str(e)
        }), 502

@app.get("/produto-oferta/<produto_id>")
def produto_oferta(produto_id):
    access_token = get_valid_token()

    if not access_token:
        return jsonify({
            "ok": False,
            "error": "Mercado Livre não conectado"
        }), 401

    try:
        r = requests.get(
            f"{ML_API}/products/{produto_id}",
            headers={
                "Authorization": f"Bearer {access_token}"
            },
            timeout=20
        )

        data = r.json()

        if not r.ok:
            return jsonify({
                "ok": False,
                "error": "Falha ao consultar produto",
                "detalhe_ml": data
            }), r.status_code

        vencedor = data.get("buy_box_winner") or {}

        preco = vencedor.get("price")
        moeda = vencedor.get("currency_id")
        item_id = vencedor.get("item_id")
        link = data.get("permalink")

        return jsonify({
            "ok": True,
            "produto_id": data.get("id"),
            "nome": data.get("name"),
            "item_id": item_id,
            "preco": preco,
            "moeda": moeda,
            "link": link,
            "tem_oferta": bool(vencedor)
        })

    except requests.RequestException as e:
        return jsonify({
            "ok": False,
            "error": "Falha de comunicação com Mercado Livre",
            "detalhe": str(e)
        }), 502

@app.get("/buscar-itens-produto/<produto_id>")
def buscar_itens_produto(produto_id):
    access_token = get_valid_token()

    if not access_token:
        return jsonify({
            "ok": False,
            "erro": "Mercado Livre não conectado"
        }), 401

    try:
        r = requests.get(
            f"{ML_API}/sites/MLB/search",
            params={
                "catalog_product_id": produto_id,
                "status": "active",
                "limit": 10
            },
            headers={
                "Authorization": f"Bearer {access_token}"
            },
            timeout=20
        )

        data = r.json()

        if not r.ok:
            return jsonify({
                "ok": False,
                "status_ml": r.status_code,
                "detalhe_ml": data
            }), r.status_code

        itens = []

        for item in data.get("results", []):
            itens.append({
                "item_id": item.get("id"),
                "titulo": item.get("title"),
                "preco": item.get("price"),
                "original_price": item.get("original_price"),
                "moeda": item.get("currency_id"),
                "link": item.get("permalink")
            })

        return jsonify({
            "ok": True,
            "produto_id": produto_id,
            "quantidade": len(itens),
            "itens": itens
        })

    except requests.RequestException as e:
        return jsonify({
            "ok": False,
            "erro": "Falha de comunicação com Mercado Livre",
            "detalhe": str(e)
        }), 502

@app.get("/buscar-meus-itens-produto/<produto_id>")
def buscar_meus_itens_produto(produto_id):
    access_token = get_valid_token()

    if not access_token:
        return jsonify({
            "ok": False,
            "erro": "Mercado Livre não conectado"
        }), 401

    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    try:
        r_user = requests.get(
            f"{ML_API}/users/me",
            headers=headers,
            timeout=20
        )

        user_data = r_user.json()

        if not r_user.ok:
            return jsonify({
                "ok": False,
                "erro": "Falha ao identificar usuário",
                "detalhe_ml": user_data
            }), r_user.status_code

        user_id = user_data.get("id")

        r_search = requests.get(
            f"{ML_API}/users/{user_id}/items/search",
            params={
                "status": "active",
                "limit": 100
            },
            headers=headers,
            timeout=20
        )

        search_data = r_search.json()

        if not r_search.ok:
            return jsonify({
                "ok": False,
                "erro": "Falha ao buscar anúncios",
                "detalhe_ml": search_data
            }), r_search.status_code

        item_ids = search_data.get("results", [])
        encontrados = []

        for item_id in item_ids:
            r_item = requests.get(
                f"{ML_API}/items/{item_id}",
                headers=headers,
                timeout=20
            )

            if not r_item.ok:
                continue

            item = r_item.json()

            if item.get("catalog_product_id") == produto_id:
                encontrados.append({
                    "item_id": item.get("id"),
                    "titulo": item.get("title"),
                    "catalog_product_id": item.get("catalog_product_id"),
                    "preco": item.get("price"),
                    "preco_original": item.get("original_price"),
                    "moeda": item.get("currency_id"),
                    "link": item.get("permalink")
                })

        return jsonify({
            "ok": True,
            "user_id": user_id,
            "produto_id": produto_id,
            "quantidade": len(encontrados),
            "itens": encontrados
        })

    except requests.RequestException as e:
        return jsonify({
            "ok": False,
            "erro": "Falha de comunicação com Mercado Livre",
            "detalhe": str(e)
       }), 502

@app.get("/item-oferta/<item_id>")
def item_oferta(item_id):
    access_token = get_valid_token()

    if not access_token:
        return jsonify({
            "ok": False,
            "erro": "Mercado Livre não conectado"
        }), 401

    try:
        r = requests.get(
            f"{ML_API}/items/{item_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=20
        )

        data = r.json()

        if not r.ok:
            return jsonify({
                "ok": False,
                "status_ml": r.status_code,
                "detalhe_ml": data
            }), r.status_code

        preco = data.get("price")
        preco_original = data.get("original_price")

        desconto = None
        if preco is not None and preco_original is not None and preco_original > preco:
            desconto = round(
                ((preco_original - preco) / preco_original) * 100, 2
            )

        return jsonify({
            "ok": True,
            "item_id": data.get("id"),
            "titulo": data.get("title"),
            "preco": preco,
            "preco_original": preco_original,
            "desconto_percentual": desconto,
            "moeda": data.get("currency_id"),
            "link": data.get("permalink"),
            "catalog_product_id": data.get("catalog_product_id"),
            "status": data.get("status")
        })

    except requests.RequestException as e:
        return jsonify({
            "ok": False,
            "erro": "Falha de comunicação com Mercado Livre",
            "detalhe": str(e)
        }), 502
