"""
Login por tela, com sessão em cookie assinado.

Duas decisões que valem explicar:

**Fail-closed.** Sem `VIAB_SENHA` no ambiente o sistema não serve nada além do
health check: mostra uma página dizendo como configurar. Um sistema que guarda
a viabilidade e o incorrido de uma SPE não pode ficar aberto por esquecimento
de variável de ambiente.

**A senha não vira cookie.** O cookie carrega usuário + validade assinados com
uma chave derivada da senha por HMAC. Quem roubar o cookie não descobre a
senha, e trocar a senha invalida todas as sessões automaticamente.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

COOKIE = "viab_sessao"
DURACAO = 12 * 3600          # 12 horas: um dia de trabalho, sem eternizar sessão
ABERTAS = {"/saude", "/entrar", "/sair"}
PREFIXOS_ABERTOS = ("/static/",)


# ------------------------------------------------------------------ config
def credenciais_configuradas() -> tuple[str, str] | None:
    senha = os.getenv("VIAB_SENHA", "")
    if not senha:
        return None
    return os.getenv("VIAB_USUARIO", "mmi"), senha


def _chave() -> bytes:
    """Chave de assinatura derivada da senha — trocar a senha derruba as sessões."""
    conf = credenciais_configuradas()
    segredo = (conf[1] if conf else "") + os.getenv("VIAB_SEGREDO", "viabilidade")
    return hashlib.sha256(segredo.encode()).digest()


# ------------------------------------------------------------------ sessão
def criar_sessao(usuario: str) -> str:
    corpo = json.dumps({"u": usuario, "exp": int(time.time()) + DURACAO},
                       separators=(",", ":")).encode()
    dados = base64.urlsafe_b64encode(corpo).rstrip(b"=")
    assinatura = base64.urlsafe_b64encode(
        hmac.new(_chave(), dados, hashlib.sha256).digest()).rstrip(b"=")
    return f"{dados.decode()}.{assinatura.decode()}"


def sessao_valida(valor: str | None) -> bool:
    if not valor or "." not in valor:
        return False
    dados, _, assinatura = valor.rpartition(".")
    esperada = base64.urlsafe_b64encode(
        hmac.new(_chave(), dados.encode(), hashlib.sha256).digest()).rstrip(b"=")
    if not hmac.compare_digest(assinatura.encode(), esperada):
        return False
    try:
        corpo = json.loads(base64.urlsafe_b64decode(dados + "=" * (-len(dados) % 4)))
    except Exception:
        return False
    return int(corpo.get("exp", 0)) > time.time()


def usuario_da_sessao(valor: str | None) -> str | None:
    """
    O nome de quem está logado, lido do cookie já verificado.

    Existe para o registro de alterações ter autor. Hoje há uma credencial só e
    a resposta é sempre a mesma — mas quando houver tabela de usuários, o cookie
    já carrega o nome e nada mais precisa mudar.
    """
    if not sessao_valida(valor):
        return None
    dados = valor.rpartition(".")[0]
    try:
        corpo = json.loads(base64.urlsafe_b64decode(dados + "=" * (-len(dados) % 4)))
    except Exception:
        return None
    return corpo.get("u")


def conferir_senha(usuario: str, senha: str) -> bool:
    conf = credenciais_configuradas()
    if conf is None:
        return False
    # compare_digest nos dois: o tempo de resposta não entrega o tamanho
    return (hmac.compare_digest(usuario.encode(), conf[0].encode())
            and hmac.compare_digest(senha.encode(), conf[1].encode()))


# ------------------------------------------------------------------ guarda
async def exigir_login(request: Request):
    caminho = request.url.path
    if caminho in ABERTAS or caminho.startswith(PREFIXOS_ABERTOS):
        return

    if credenciais_configuradas() is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "sem senha configurada")

    if sessao_valida(request.cookies.get(COOKIE)):
        return

    # a API responde 401 seco; a tela redireciona para o login
    if caminho.startswith("/api"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "sessão ausente ou expirada")
    raise HTTPException(status.HTTP_307_TEMPORARY_REDIRECT, "login necessário",
                        headers={"Location": f"/entrar?de={caminho}"})


async def usuario_atual(request: Request) -> str:
    """Dependência das rotas que escrevem: sem autor, não se grava histórico."""
    return usuario_da_sessao(request.cookies.get(COOKIE)) or "desconhecido"


def registrar(app) -> None:
    @app.exception_handler(HTTPException)
    async def _tratar(request: Request, exc: HTTPException):
        if exc.status_code == status.HTTP_307_TEMPORARY_REDIRECT and exc.headers:
            return RedirectResponse(exc.headers["Location"], status_code=307)
        if (exc.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
                and not request.url.path.startswith("/api")):
            return HTMLResponse(SEM_SENHA, status_code=503)
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code,
                            headers=exc.headers)


SEM_SENHA = """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sistema bloqueado</title>
<style>
:root{--papel:#F6F5F1;--papel2:#EFEEE8;--tinta:#191C1A;--tinta2:#4A514D;--fio:#D6D5CD}
@media(prefers-color-scheme:dark){:root{--papel:#121513;--papel2:#181C19;
  --tinta:#E9EAE5;--tinta2:#A0A8A3;--fio:#2A2F2C}}
body{font-family:system-ui,-apple-system,Segoe UI,sans-serif;background:var(--papel);
 color:var(--tinta);margin:0;display:grid;place-items:center;min-height:100vh;padding:2rem}
main{max-width:54ch;line-height:1.6}
h1{font-size:1.25rem;margin:0 0 .7rem;letter-spacing:-.01em}
p{margin:0 0 1em;color:var(--tinta2);font-size:.95rem}
code{background:var(--papel2);border:1px solid var(--fio);border-radius:3px;
 padding:.1em .4em;font-size:.88em}
</style></head><body><main>
<h1>Bloqueado por falta de senha</h1>
<p>Este sistema guarda a viabilidade e o incorrido de empreendimentos. Ele se
recusa a servir dados enquanto não houver senha configurada — de propósito.</p>
<p>No painel do serviço, em <b>Environment</b>, defina
<code>VIAB_SENHA</code>. Para trocar o usuário padrão (<code>mmi</code>), defina
também <code>VIAB_USUARIO</code>. O serviço reinicia sozinho e passa a mostrar a
tela de login.</p>
</main></body></html>"""
