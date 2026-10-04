import os
from flask import Flask, jsonify, redirect, request
import requests

app = Flask(__name__)
ML_API = "https://api.mercadolibre.com"

@app.get("/")
def home():
    return jsonify({"app":"MV Oferta Certa","status":"online","mercado_livre":"pronto para conectar"})

@app.get("/health")
def health():
    return jsonify({"ok": True})

@app.get("/login")
def login():
    client_id = os.environ.get("ML_CLIENT_ID")
    redirect_uri = os.environ.get("ML_REDIRECT_URI")
    if not client_id or not redirect_uri:
        return jsonify({"erro":"Configure ML_CLIENT_ID e ML_REDIRECT_URI no Render"}), 500
    url = f"https://auth.mercadolivre.com.br/authorization?response_type=code&client_id={client_id}&redirect_uri={redirect_uri}"
    return redirect(url)

@app.get("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return jsonify({"erro":"Código de autorização não recebido"}), 400
    payload = {
        "grant_type":"authorization_code",
        "client_id":os.environ.get("ML_CLIENT_ID"),
        "client_secret":os.environ.get("ML_CLIENT_SECRET"),
        "code":code,
        "redirect_uri":os.environ.get("ML_REDIRECT_URI"),
    }
    r = requests.post(f"{ML_API}/oauth/token", data=payload, timeout=20)
    data = r.json()
    # Protótipo: não persiste tokens. Em produção, guardar em armazenamento seguro.
    if r.ok:
        return jsonify({"conectado":True,"mensagem":"Mercado Livre autorizado. Próximo passo: armazenamento seguro do token e busca/ranking de ofertas."})
    return jsonify(data), r.status_code

@app.get("/ofertas")
def ofertas():
    return jsonify({
        "status":"estrutura pronta",
        "proximo_passo":"conectar fonte de ofertas permitida pela API e aplicar ranking",
        "criterios":["desconto","preço","frete","relevância"]
    })
