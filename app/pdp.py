"""
Leitura do cronograma no PDP.

O PDP não publica API. O que existe é uma tela e, atrás dela, um endpoint que a
própria tela chama para desenhar o gráfico de Gantt — e esse devolve JSON com
tudo o que interessa: id, nome, início, fim, duração, progresso, caminho
crítico e as ligações de precedência. É dele que este módulo lê.

Três consequências que vale enxergar de frente:

**Isto é raspagem educada, não integração contratada.** Se o PDP mudar a rota
ou o nome de um campo, esta leitura quebra. Ela quebra dizendo o que quebrou —
nunca devolvendo dado pela metade, que seria pior: um cronograma incompleto
viraria uma curva de caixa errada sem ninguém perceber.

**Precisa de um usuário.** Crie um no PDP só para isto, de leitura, e ponha
e-mail e senha nas variáveis de ambiente do serviço. A senha não fica no
código, não fica no banco e não aparece em tela.

**O PDP manda no cronograma.** Aqui é cópia. Cada sincronização substitui o
conjunto inteiro de marcos do empreendimento; nada é editado deste lado.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

import requests

URL_PADRAO = "https://mmi.pdpweb.com.br"
TIMEOUT = 30


class PDPIndisponivel(RuntimeError):
    """O PDP não respondeu, recusou a credencial ou mudou de formato."""


class PDPProtegido(PDPIndisponivel):
    """
    O PDP está atrás de um desafio de bot e não aceita acesso servidor a servidor.

    Não há o que consertar deste lado: o Cloudflare do PDP responde 403 antes do
    login, para qualquer credencial. Quem resolve é quem administra o PDP —
    liberando o IP do serviço na regra de WAF, ou publicando uma API de verdade.
    Enquanto isso, o cronograma entra por arquivo.
    """


@dataclass
class MarcoPDP:
    pdp_id: str
    nome: str
    inicio: Optional[date]
    fim: Optional[date]
    duracao: Optional[int]
    progresso: int
    critico: bool
    area_codigo: Optional[str] = None
    processo: Optional[str] = None
    fase: Optional[str] = None
    predecessores: list = field(default_factory=list)   # (pdp_id, tipo, defasagem)


def _data(txt) -> Optional[date]:
    """O PDP devolve dd/mm/aaaa. Qualquer outra coisa vira None, não exceção."""
    if not txt:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(txt)[:10], fmt).date()
        except ValueError:
            continue
    return None


def _conferir_desafio(r) -> None:
    """
    Distingue "credencial errada" de "nem chegou no PDP".

    O Cloudflare marca a resposta com `cf-mitigated: challenge` quando decide
    que o cliente não é um navegador. Sem esta checagem, o erro que aparece na
    tela é "respondeu 403" e a pessoa vai passar a tarde conferindo a senha.
    """
    if r.status_code != 403:
        return
    marcas = {k.lower(): (v or "").lower() for k, v in r.headers.items()}
    desafio = ("challenge" in marcas.get("cf-mitigated", "")
               or "cloudflare" in marcas.get("server", "")
               or "cf-ray" in marcas)
    if desafio:
        raise PDPProtegido(
            "o PDP está atrás de um desafio de bot do Cloudflare e recusa "
            "acesso de servidor antes mesmo do login — nenhuma senha passa por "
            "aí. Quem administra o PDP precisa liberar o IP deste serviço na "
            "regra de WAF, ou disponibilizar uma API. Enquanto isso, use a "
            "importação por arquivo.")


def _critico(v) -> bool:
    # o campo vem como texto: 'planned_critical_path' ou 'not-critical-path'
    s = str(v or "")
    return "critical_path" in s and not s.startswith("not")


class PDP:
    """
    Sessão autenticada no PDP.

    Uso:
        with PDP() as pdp:
            marcos = pdp.marcos(project_id=26)
    """

    def __init__(self, url: str | None = None,
                 email: str | None = None, senha: str | None = None):
        self.url = (url or os.getenv("PDP_URL") or URL_PADRAO).rstrip("/")
        self.email = email or os.getenv("PDP_EMAIL")
        self.senha = senha or os.getenv("PDP_SENHA")
        self.sessao = requests.Session()
        self.sessao.headers["User-Agent"] = "viabilidade-mmi/1.0"
        self._entrou = False

    # ------------------------------------------------------------ sessão
    def __enter__(self):
        self.entrar()
        return self

    def __exit__(self, *_):
        self.sessao.close()

    @staticmethod
    def configurado() -> bool:
        return bool(os.getenv("PDP_EMAIL") and os.getenv("PDP_SENHA"))

    def entrar(self) -> None:
        if self._entrou:
            return
        if not self.email or not self.senha:
            raise PDPIndisponivel(
                "PDP_EMAIL e PDP_SENHA não estão definidas. Crie no PDP um "
                "usuário de leitura para o sistema de viabilidade e configure "
                "as duas variáveis no serviço — a senha não passa por aqui nem "
                "fica gravada no banco.")
        try:
            r = self.sessao.post(f"{self.url}/login",
                                 data={"email": self.email, "password": self.senha},
                                 timeout=TIMEOUT, allow_redirects=True)
        except requests.RequestException as e:
            raise PDPIndisponivel(f"não consegui falar com o PDP: {e}") from e

        _conferir_desafio(r)

        # o PDP responde 200 tanto para entrada aceita quanto recusada; o que
        # separa os dois é continuar na tela de login
        if 'name="password"' in r.text and "/dashboard" not in r.url:
            raise PDPIndisponivel(
                "o PDP recusou a credencial. Confira o e-mail e a senha do "
                "usuário de leitura.")
        self._entrou = True

    # ------------------------------------------------------------ leitura
    def _tela(self, project_id: int) -> str:
        r = self.sessao.get(f"{self.url}/dashboard", timeout=TIMEOUT, params={
            "action": "simulation", "project_id": project_id,
            "critical_path_filter": "false", "ordenation": "end_date", "order": "asc"})
        _conferir_desafio(r)
        if r.status_code != 200:
            raise PDPIndisponivel(f"a tela de simulação respondeu {r.status_code}")
        return r.text

    def _gantt(self, project_id: int, **filtros) -> dict:
        p = {"action": "simulation", "project_id": project_id,
             "critical_path_filter": "false", "ordenation": "end_date",
             "order": "asc", **filtros}
        r = self.sessao.post(f"{self.url}/dashboard/load-simulation-gantt-chart",
                             params=p, timeout=TIMEOUT,
                             headers={"X-Requested-With": "XMLHttpRequest",
                                      "Accept": "application/json"})
        if r.status_code != 200:
            raise PDPIndisponivel(
                f"o cronograma respondeu {r.status_code} — se for 302 ou 401, a "
                f"sessão do PDP caiu; se for 405, a rota mudou de nome.")
        try:
            dados = r.json()
        except ValueError:
            raise PDPIndisponivel(
                "o cronograma veio em HTML, não em JSON. Ou a sessão expirou, "
                "ou o PDP mudou o endpoint.") from None
        if "tasks" not in dados:
            raise PDPIndisponivel(
                f"o JSON do cronograma não tem 'tasks' — veio {list(dados)[:6]}. "
                f"O formato do PDP mudou e esta leitura precisa ser revista.")
        return dados

    # ------------------------------------------------- catálogos da tela
    @staticmethod
    def _opcoes(html: str, id_select: str) -> dict[str, str]:
        """Lê um <select> da tela de filtros: valor -> texto."""
        m = re.search(rf'<select[^>]*id="{id_select}"[^>]*>(.*?)</select>',
                      html, re.S)
        if not m:
            return {}
        return {v: re.sub(r"\s+", " ", t).strip()
                for v, t in re.findall(r'<option[^>]*value="([^"]*)"[^>]*>(.*?)</option>',
                                       m.group(1), re.S)
                if v not in ("", "0")}

    def projetos(self) -> dict[str, str]:
        """Os projetos que este usuário enxerga no PDP: project_id -> nome."""
        return self._opcoes(self._tela(1), "project-select")

    # ------------------------------------------------------------ marcos
    def marcos(self, project_id: int) -> list[MarcoPDP]:
        """
        Todos os marcos do projeto, com área, processo, fase e predecessores.

        O JSON do cronograma não traz área/processo/fase — mas o mesmo endpoint
        aceita os filtros da tela. Então: uma chamada sem filtro para o conjunto
        completo e as ligações, e uma por filtro para descobrir a quem cada
        marco pertence. São ~30 chamadas curtas, e é o preço de o PDP não ter
        API de verdade.
        """
        html = self._tela(project_id)
        areas = self._opcoes(html, "area-select")
        processos = self._opcoes(html, "process-select")
        fases = self._opcoes(html, "phase-select")

        dados = self._gantt(project_id)
        por_id: dict[str, MarcoPDP] = {}
        for t in dados["tasks"]:
            pid = str(t.get("display_id") or t.get("id") or "").lstrip("#")
            if not pid:
                continue
            por_id[pid] = MarcoPDP(
                pdp_id=pid,
                nome=(t.get("text") or "").strip(),
                inicio=_data(t.get("start_date")),
                fim=_data(t.get("end_date")),
                duracao=t.get("real_duration") or t.get("duration"),
                progresso=int(t.get("progress_percent") or 0),
                critico=_critico(t.get("is_planned_critical_path")))

        interno = {str(t.get("id")): str(t.get("display_id") or t.get("id")).lstrip("#")
                   for t in dados["tasks"]}
        for l in dados.get("links", []):
            alvo = interno.get(str(l.get("target")))
            origem = interno.get(str(l.get("source")), str(l.get("source")))
            if alvo and alvo in por_id:
                por_id[alvo].predecessores.append(
                    (origem, (l.get("relation_type") or "TI").upper(),
                     int(l.get("lag") or 0)))

        for campo, catalogo, param in (("area_codigo", areas, "area_id"),
                                       ("processo", processos, "process_id"),
                                       ("fase", fases, "phase_id")):
            for cod, nome in catalogo.items():
                for t in self._gantt(project_id, **{param: cod}).get("tasks", []):
                    pid = str(t.get("display_id") or t.get("id") or "").lstrip("#")
                    if pid in por_id:
                        setattr(por_id[pid], campo, cod if campo == "area_codigo" else nome)

        if not por_id:
            raise PDPIndisponivel(
                f"o projeto {project_id} não devolveu marco nenhum. Confira o "
                f"código do projeto no PDP e se o usuário de leitura enxerga esse projeto.")
        return list(por_id.values())
