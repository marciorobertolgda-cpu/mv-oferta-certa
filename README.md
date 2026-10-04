# MV Oferta Certa — Robô

Backend inicial para publicar no Render e conectar com o Mercado Livre via OAuth.

## Render
- Language: Python 3
- Build Command: `pip install -r requirements.txt`
- Start Command: `gunicorn app:app`

## Variáveis de ambiente
Configure no Render (não coloque segredos no GitHub):
- `ML_CLIENT_ID`
- `ML_CLIENT_SECRET`
- `ML_REDIRECT_URI` = `https://SEU-SERVICO.onrender.com/callback`

Depois, cadastre exatamente essa mesma URI de redirect na aplicação do Mercado Livre.

## Rotas
- `/` status
- `/health` teste
- `/login` inicia OAuth do Mercado Livre
- `/callback` recebe autorização
- `/ofertas` estrutura inicial do robô

> Este protótipo não grava tokens. O próximo passo é adicionar armazenamento seguro e a lógica de coleta/ranking de ofertas usando recursos permitidos pela API.
