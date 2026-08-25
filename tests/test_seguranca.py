"""
Conferências da tela de login.

Não sobem o app inteiro: testam as três regras que, se quebrarem, abrem o
sistema — a assinatura do cookie, a expiração e o fail-closed.
"""
import base64
import json
import time

import pytest

from app import seguranca as sg


@pytest.fixture
def com_senha(monkeypatch):
    monkeypatch.setenv("VIAB_SENHA", "segredo-de-teste")
    monkeypatch.setenv("VIAB_USUARIO", "mmi")
    monkeypatch.delenv("VIAB_SEGREDO", raising=False)


def test_sem_senha_o_sistema_se_declara_desconfigurado(monkeypatch):
    monkeypatch.delenv("VIAB_SENHA", raising=False)
    assert sg.credenciais_configuradas() is None
    # e nenhuma senha passa — nem string vazia
    assert not sg.conferir_senha("mmi", "")
    assert not sg.conferir_senha("mmi", "qualquer")


def test_senha_certa_e_errada(com_senha):
    assert sg.conferir_senha("mmi", "segredo-de-teste")
    assert not sg.conferir_senha("mmi", "segredo-de-test")
    assert not sg.conferir_senha("outro", "segredo-de-teste")


def test_cookie_valido_ida_e_volta(com_senha):
    assert sg.sessao_valida(sg.criar_sessao("mmi"))


def test_cookie_adulterado_nao_passa(com_senha):
    bom = sg.criar_sessao("mmi")
    dados, _, assinatura = bom.rpartition(".")
    # troca o usuário mantendo a assinatura antiga
    corpo = json.loads(base64.urlsafe_b64decode(dados + "=" * (-len(dados) % 4)))
    corpo["u"] = "invasor"
    novo = base64.urlsafe_b64encode(
        json.dumps(corpo, separators=(",", ":")).encode()).rstrip(b"=").decode()
    assert not sg.sessao_valida(f"{novo}.{assinatura}")
    assert not sg.sessao_valida(f"{dados}.{assinatura[:-2]}xx")
    assert not sg.sessao_valida("lixo")
    assert not sg.sessao_valida(None)


def test_cookie_expirado_nao_passa(com_senha, monkeypatch):
    monkeypatch.setattr(sg, "DURACAO", -60)     # já nasce vencido
    assert not sg.sessao_valida(sg.criar_sessao("mmi"))


def test_trocar_a_senha_derruba_as_sessoes(com_senha, monkeypatch):
    antigo = sg.criar_sessao("mmi")
    assert sg.sessao_valida(antigo)
    monkeypatch.setenv("VIAB_SENHA", "outra-senha")
    assert not sg.sessao_valida(antigo)


def test_sessao_de_um_sistema_sem_senha_nao_vale(com_senha, monkeypatch):
    """Sem VIAB_SENHA a chave vira constante — o cookie não pode passar."""
    cookie = sg.criar_sessao("mmi")
    monkeypatch.delenv("VIAB_SENHA", raising=False)
    assert not sg.sessao_valida(cookie)
