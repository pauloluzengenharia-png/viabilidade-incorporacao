"""
Os módulos de entrada de dados.

Cada bloco do estudo — premissas, comercial, plano de vendas, obra, terreno,
unidades, cadastro — ganha aqui a sua definição: quais campos tem, em que
unidade cada um é digitado, o que o torna inválido e onde ele mora no banco.

Três coisas valem explicar, porque são o motivo deste arquivo existir em vez de
sete rotas escrevendo `UPDATE` cada uma do seu jeito:

**A definição é declarativa.** Um módulo é uma lista de campos; a tela genérica
sabe desenhar qualquer lista de campos, e o texto de ajuda de cada um vem do
glossário — o mesmo que alimenta `/guia`. Acrescentar uma premissa é acrescentar
uma linha aqui, não uma tela.

**Nada é gravado sem diferença.** Toda gravação lê o valor que está lá, compara
com o que chegou do formulário e só escreve o que mudou. É isso que permite o
registro de alterações ser honesto: ele lista o que de fato mudou, não os
quarenta campos que o formulário enviou.

**As travas do banco continuam sendo do banco.** A tabela de venda tem de somar
100% e a curva física também; as telas conferem antes para dar mensagem
decente, mas quem recusa de verdade é o CHECK. Validação de tela é cortesia,
não garantia.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from sqlalchemy.orm import Session

from .db import q
from .glossario import POR_CHAVE
from .novo_estudo import fim_do_mes  # noqa: F401 — reexportado para as rotas


# =====================================================================
# leitura de valores digitados
# =====================================================================
def num(v: Any, padrao: float = 0.0) -> float:
    """
    Aceita os dois jeitos de digitar um número aqui.

    Com vírgula não há dúvida: a vírgula é o decimal e o ponto é milhar.
    Sem vírgula existe uma ambiguidade real — `250.000` pode ser duzentos e
    cinquenta mil ou duzentos e cinquenta. Resolver isso errado é como um
    orçamento de marketing de R$ 250 entra no estudo, então a regra é
    explícita: um ponto seguido de exatamente três dígitos é milhar, **exceto**
    quando a parte inteira é só `0` — `0.045` é decimal, `250.000` é milhar.
    Dois ou mais pontos são sempre milhar.
    """
    if v is None:
        return padrao
    s = (str(v).strip()
         .replace("R$", "").replace("%", "")
         .replace(" ", "").replace("\u00a0", ""))
    if not s:
        return padrao
    negativo = s.startswith("-")
    s = s.lstrip("+-")

    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    elif s.count(".") == 1:
        inteiro_, _, fracao = s.partition(".")
        if len(fracao) == 3 and inteiro_.lstrip("0") != "":
            s = inteiro_ + fracao          # milhar

    try:
        n = float(s)
    except ValueError:
        return padrao
    return -n if negativo else n


def inteiro(v: Any, padrao: int = 0) -> int:
    return int(num(v, padrao))


def data(v: Any) -> Optional[dt.date]:
    if not v:
        return None
    try:
        return dt.date.fromisoformat(str(v).strip())
    except ValueError:
        return None


def texto(v: Any) -> str:
    return (str(v).strip() if v is not None else "")


def como_texto(v: Any) -> Optional[str]:
    """Como o valor aparece no registro de alterações."""
    if v is None:
        return None
    if isinstance(v, bool):
        return "sim" if v else "não"
    if isinstance(v, float):
        return f"{v:.8f}".rstrip("0").rstrip(".") or "0"
    return str(v)


def moeda(v: Any, minimo: int = 2) -> str:
    """
    `3704512.5` vira `3.704.512,50`; `18228.6645` vira `18.228,6645`.

    O mínimo é duas casas — dinheiro escrito com uma casa parece truncado. Mas o
    máximo é quantas o valor tiver: arredondar para duas na exibição faria a
    próxima gravação arredondar de verdade, e o preço por m² da Kiev tem quatro
    casas porque veio de uma divisão. Formatação não pode apagar dado.
    """
    if v is None or v == "":
        return ""
    try:
        n = float(v)
    except (TypeError, ValueError):
        return str(v)
    casas = max(minimo, _casas_significativas(n))
    inteiro_, _, fracao = f"{abs(n):.{casas}f}".partition(".")
    grupos = []
    while len(inteiro_) > 3:
        grupos.insert(0, inteiro_[-3:])
        inteiro_ = inteiro_[:-3]
    grupos.insert(0, inteiro_)
    return ("-" if n < 0 else "") + ".".join(grupos) + "," + fracao


def _casas_significativas(n: float, teto: int = 8) -> int:
    """Quantas casas o número de fato usa, até o teto."""
    texto_ = f"{abs(n):.{teto}f}".rstrip("0")
    fracao = texto_.partition(".")[2]
    return len(fracao)


def percentual(v: Any) -> str:
    """`0.045` continua `0,045` — a fração é a unidade em que o campo é digitado."""
    if v is None or v == "":
        return ""
    try:
        n = float(v)
    except (TypeError, ValueError):
        return str(v)
    if n == int(n):
        return str(int(n))
    return f"{n:.8f}".rstrip("0").replace(".", ",")


# =====================================================================
# definição de um módulo
# =====================================================================
@dataclass
class Campo:
    chave: str
    rotulo: str
    tipo: str = "numero"          # numero | inteiro | texto | data | escolha
    sufixo: str = ""              # o que aparece cinza ao lado do rótulo
    opcoes: tuple = ()            # para tipo 'escolha'
    ajuda: str = ""               # chave do glossário; vazio usa `chave`
    #: moeda | percentual | ""  — decide o adorno e a formatação do campo.
    #: Deduzido do sufixo quando não vem explícito, para a definição do módulo
    #: não repetir "R$" duas vezes.
    formato: str = ""

    def __post_init__(self):
        if not self.formato and self.tipo in ("numero", "inteiro"):
            if "R$" in self.sufixo:
                self.formato = "moeda"
            elif "%" in self.sufixo:
                self.formato = "percentual"

    @property
    def adorno(self) -> str:
        """
        Só o `R$`, colado à esquerda do campo.

        Percentual **não** ganha adorno de propósito: o campo é digitado em
        fração — `0,06` para 6% — e um `%` encostado num campo escrito `0,06`
        leria como "zero vírgula zero seis por cento". O rótulo já diz
        "% do bruto", que é a informação certa sem a leitura errada.
        """
        return "R$" if self.formato == "moeda" else ""

    def exibir(self, valor: Any) -> str:
        if valor is None:
            return ""
        if self.formato == "moeda":
            return moeda(valor)
        if self.formato == "percentual":
            return percentual(valor)
        if self.tipo == "inteiro":
            try:
                return str(int(float(valor)))
            except (TypeError, ValueError):
                return str(valor)
        return str(valor)

    def ler(self, bruto: Any) -> Any:
        if self.tipo == "numero":
            return num(bruto)
        if self.tipo == "inteiro":
            return inteiro(bruto)
        if self.tipo == "data":
            return data(bruto)
        return texto(bruto)

    @property
    def verbete(self):
        return POR_CHAVE.get(self.ajuda or self.chave)


@dataclass
class Grupo:
    titulo: str
    nota: str = ""
    campos: list = field(default_factory=list)


@dataclass
class Modulo:
    slug: str
    titulo: str
    resumo: str
    por_cenario: bool = False
    grupos: list = field(default_factory=list)
    soma_cem: tuple = ()          # campos que precisam fechar 100%

    @property
    def campos(self) -> list:
        return [c for g in self.grupos for c in g.campos]


# =====================================================================
# os módulos
# =====================================================================
CADASTRO = Modulo("cadastro", "Cadastro do empreendimento", """
Identifica a SPE e delimita o que entra na conta. Mexer na área privativa muda
o preço por m², o custo por m² e a eficiência de uma vez só — é o campo mais
contaminante da tela.""", grupos=[
    Grupo("Identificação", campos=[
        Campo("nome", "Nome do empreendimento", "texto"),
        Campo("cnpj", "CNPJ", "texto", "opcional"),
        Campo("sienge_enterprise_id", "Código no Sienge", "inteiro", "opcional"),
        Campo("sienge_company_id", "Código da empresa no Sienge", "inteiro", "opcional"),
    ]),
    Grupo("Áreas e datas", campos=[
        Campo("area_privativa", "Área privativa total", "numero", "m²"),
        Campo("area_construida", "Área construída total", "numero", "m²"),
        Campo("data_lancamento", "Lançamento", "data"),
        Campo("data_entrega_prevista", "Entrega prevista (chaves)", "data"),
        Campo("mes_corte_realizado", "Mês de corte do realizado", "data"),
    ]),
])

PREMISSAS = Modulo("premissas", "Premissas do cenário", """
Os números que a diretoria escolhe. Percentuais em fração — <code>0,045</code> é
4,5%; valores fixos em reais. Mudar qualquer um marca o cenário como
desatualizado até você recalcular.""", por_cenario=True, grupos=[
    Grupo("Deduções da receita",
          "Incidem sobre o que a SPE recebe, não sobre o que fatura. "
          "As despesas comerciais saíram daqui: agora têm composição própria, "
          "em Custos.", [
        Campo("ret", "Impostos (RET)", "numero", "% da receita"),
        Campo("distratos", "Distratos", "numero", "% da receita"),
    ]),
    Grupo("Taxas do incorporador", "", [
        Campo("taxa_adm_obra", "Taxa de administração — obra", "numero", "% do custo raso"),
        Campo("taxa_viabilizacao", "Taxa de administração — carteira", "numero", "% da receita"),
        Campo("outras_desp_adm_perc", "Outras despesas administrativas", "numero", "% do custo raso"),
    ]),
    Grupo("Outras entradas",
          "Decoração, projetos, marketing, legalização e regularização "
          "fundiária saíram daqui: cada um virou um setor com composição "
          "própria, em Custos.", [
        Campo("outras_entradas", "Outras receitas administrativas", "numero", "R$"),
    ]),
    Grupo("Correção monetária",
          "Vazio projeta em valores nominais, como a planilha fazia. Ligar costuma "
          "piorar a exposição, não melhorar: o mesmo índice que reajusta a parcela "
          "encarece o concreto, e o concreto vem antes.", [
        Campo("indice_ate_chaves", "Índice até as chaves", "escolha", "",
              ("", "INCC-DI", "IGP-M", "IPCA")),
        Campo("indice_apos_chaves", "Índice depois das chaves", "escolha", "",
              ("", "INCC-DI", "IGP-M", "IPCA")),
        Campo("indice_projetado_aa", "Índice projetado", "numero", "% ao ano"),
        Campo("corrigir_custo_obra", "Corrigir também o custo da obra", "escolha", "",
              ("sim", "não")),
    ]),
    Grupo("Retorno e calendário", "", [
        Campo("tma_anual", "TMA", "numero", "% ao ano"),
        Campo("meses_pos_chaves", "Meses de pós-chaves", "inteiro", "meses"),
    ]),
])

COMERCIAL = Modulo("comercial", "Preço e tabela de venda", """
De cada R$ 100 que o cliente assina, quanto entra em cada momento. A soma tem de
fechar exatamente 100% — o banco recusa o contrário, porque uma tabela que soma
97% inventa um desconto que ninguém aprovou.""", por_cenario=True,
    soma_cem=("comissao", "ato", "mensais", "anuais", "semestrais", "unica", "chaves"),
    grupos=[
        Grupo("Preço do estoque",
              "Quanto você assume receber pelas unidades ainda não vendidas.", [
            Campo("preco_m2_estoque", "Preço do estoque", "numero", "R$/m²"),
            Campo("preco_investidor_unidade", "Preço da unidade de investidor", "numero", "R$"),
            Campo("usar_preco_tabela", "Usar o preço de tabela cadastrado", "escolha", "",
                  ("sim", "não")),
        ]),
        Grupo("Como o preço se divide no tempo", "", [
            Campo("comissao", "Comissão", "numero", "% do bruto"),
            Campo("ato", "Ato", "numero", "% do bruto"),
            Campo("mensais", "Mensais", "numero", "% do bruto"),
            Campo("n_mensais", "Nº de parcelas mensais", "inteiro", "meses"),
            Campo("semestrais", "Semestrais", "numero", "% do bruto"),
            Campo("anuais", "Anuais", "numero", "% do bruto"),
            Campo("unica", "Parcela única", "numero", "% do bruto"),
            Campo("chaves", "Chaves (repasse)", "numero", "% do bruto"),
        ]),
    ])

MODULOS = {m.slug: m for m in (CADASTRO, PREMISSAS, COMERCIAL)}


# =====================================================================
# registro de alterações
# =====================================================================
def registrar(s: Session, *, emp_id: int, cenario_id: Optional[int], modulo: str,
              entidade: str, campo: str, antes: Any, depois: Any, autor: str) -> None:
    q(s, """INSERT INTO alteracao (empreendimento_id, cenario_id, modulo, entidade,
                                   campo, valor_anterior, valor_novo, autor)
            VALUES (:e,:c,:m,:n,:k,:a,:d,:u)""",
      e=emp_id, c=cenario_id, m=modulo, n=entidade, k=campo,
      a=como_texto(antes), d=como_texto(depois), u=autor)


def diferentes(antes: Any, depois: Any) -> bool:
    """Compara com a folga que o tipo pede — numérico não compara por igualdade."""
    if isinstance(antes, float) or isinstance(depois, float):
        a = num(antes) if antes is not None else None
        d = num(depois) if depois is not None else None
        if a is None or d is None:
            return a is not d
        return abs(a - d) > 1e-9
    if antes is None and depois in ("", None):
        return False
    return str(antes or "") != str(depois or "")


# =====================================================================
# leitura e gravação do módulo de premissas
# =====================================================================
#: como cada premissa é guardada — a tabela `premissa` exige a unidade
UNIDADE_DA_PREMISSA = {
    "ret": "percentual", "distratos": "percentual",
    "despesas_comerciais": "moeda", "terreno_registro_perc": "percentual",
    "taxa_adm_obra": "percentual", "taxa_viabilizacao": "percentual",
    "decoracao": "moeda", "projetos_e_outros": "moeda",
    "marketing_stand": "moeda", "marketing_propaganda": "moeda",
    "outras_desp_adm_perc": "percentual", "outras_entradas": "moeda",
    "tma_anual": "percentual", "meses_pos_chaves": "meses",
    "indice_projetado_aa": "percentual", "corrigir_custo_obra": "percentual",
    "financiamento_limite": "moeda", "financiamento_juros_aa": "percentual",
    "financiamento_prazo_amort": "meses", "financiamento_gatilho_obra": "percentual",
}

#: os dois índices moram no cenário, não em `premissa`
NO_CENARIO = ("indice_ate_chaves", "indice_apos_chaves")


def ler_premissas(s: Session, cenario_id: int) -> dict:
    valores = {r["chave"]: float(r["valor"])
               for r in q(s, "SELECT chave, valor FROM premissa WHERE cenario_id = :c",
                          c=cenario_id)}
    cen = q(s, """SELECT indice_ate_chaves, indice_apos_chaves
                    FROM cenario WHERE id = :c""", c=cenario_id)[0]
    valores["indice_ate_chaves"] = cen["indice_ate_chaves"] or ""
    valores["indice_apos_chaves"] = cen["indice_apos_chaves"] or ""
    valores["corrigir_custo_obra"] = (
        "não" if valores.get("corrigir_custo_obra") == 0 else "sim")
    return valores


def gravar_premissas(s: Session, *, emp_id: int, cenario_id: int,
                     enviado: dict, autor: str) -> list[str]:
    """Grava o que mudou e devolve a lista de campos alterados."""
    atual = ler_premissas(s, cenario_id)
    mudou: list[str] = []

    for campo in PREMISSAS.campos:
        k = campo.chave
        novo = campo.ler(enviado.get(k))
        if campo.tipo == "escolha":
            novo = texto(enviado.get(k))
        antes = atual.get(k)

        if k in NO_CENARIO:
            if not diferentes(antes, novo):
                continue
            q(s, f"UPDATE cenario SET {k} = :v WHERE id = :c",
              v=(novo or None), c=cenario_id)
        elif k == "corrigir_custo_obra":
            if not diferentes(antes, novo):
                continue
            _upsert_premissa(s, cenario_id, k, 0.0 if novo == "não" else 1.0,
                             "percentual")
        else:
            if antes is None and novo == 0:
                # premissa que nunca existiu e continua zerada: não inventa linha
                continue
            if not diferentes(antes, novo):
                continue
            _upsert_premissa(s, cenario_id, k, float(novo),
                             UNIDADE_DA_PREMISSA.get(k, "moeda"))

        registrar(s, emp_id=emp_id, cenario_id=cenario_id, modulo="premissas",
                  entidade="premissa", campo=k, antes=antes, depois=novo, autor=autor)
        mudou.append(k)

    return mudou


def _upsert_premissa(s: Session, cenario_id: int, chave: str, valor: float,
                     unidade: str) -> None:
    q(s, """INSERT INTO premissa (cenario_id, chave, valor, unidade, origem)
            VALUES (:c,:k,:v,:u,'editado na tela')
            ON CONFLICT (cenario_id, chave)
            DO UPDATE SET valor = :v, unidade = :u, origem = 'editado na tela'""",
      c=cenario_id, k=chave, v=valor, u=unidade)


# =====================================================================
# leitura e gravação do módulo comercial
# =====================================================================
def ler_comercial(s: Session, emp_id: int, cenario_id: int) -> dict:
    v: dict = {}
    tab = q(s, """SELECT * FROM tabela_venda
                   WHERE empreendimento_id = :e ORDER BY id LIMIT 1""", e=emp_id)
    if tab:
        t = tab[0]
        v.update(comissao=float(t["perc_comissao"]), ato=float(t["perc_ato"]),
                 mensais=float(t["perc_mensais"]), anuais=float(t["perc_anuais"]),
                 semestrais=float(t["perc_semestrais"]), unica=float(t["perc_unica"]),
                 chaves=float(t["perc_chaves"]), n_mensais=int(t["qtd_mensais"]))
    pc = q(s, """SELECT preco_m2, preco_unidade, usar_tabela FROM preco_cenario
                  WHERE cenario_id = :c AND tipo_venda = 'Normal'""", c=cenario_id)
    if pc:
        v["preco_m2_estoque"] = float(pc[0]["preco_m2"] or 0)
        v["usar_preco_tabela"] = "sim" if pc[0]["usar_tabela"] else "não"
    inv = q(s, """SELECT preco_unidade FROM preco_cenario
                   WHERE cenario_id = :c AND tipo_venda = 'Investidor'""", c=cenario_id)
    v["preco_investidor_unidade"] = float(inv[0]["preco_unidade"] or 0) if inv else 0.0
    return v


COLUNA_DA_TABELA = {
    "comissao": "perc_comissao", "ato": "perc_ato", "mensais": "perc_mensais",
    "anuais": "perc_anuais", "semestrais": "perc_semestrais",
    "unica": "perc_unica", "chaves": "perc_chaves", "n_mensais": "qtd_mensais",
}


def gravar_comercial(s: Session, *, emp_id: int, cenario_id: int,
                     enviado: dict, autor: str) -> list[str]:
    atual = ler_comercial(s, emp_id, cenario_id)
    mudou: list[str] = []

    for campo in COMERCIAL.campos:
        k = campo.chave
        novo = texto(enviado.get(k)) if campo.tipo == "escolha" else campo.ler(enviado.get(k))
        antes = atual.get(k)
        if not diferentes(antes, novo):
            continue

        if k in COLUNA_DA_TABELA:
            q(s, f"""UPDATE tabela_venda SET {COLUNA_DA_TABELA[k]} = :v
                      WHERE empreendimento_id = :e""", v=novo, e=emp_id)
        elif k == "preco_m2_estoque":
            q(s, """UPDATE preco_cenario SET preco_m2 = :v
                     WHERE cenario_id = :c AND tipo_venda = 'Normal'""",
              v=novo, c=cenario_id)
        elif k == "usar_preco_tabela":
            q(s, """UPDATE preco_cenario SET usar_tabela = :v
                     WHERE cenario_id = :c AND tipo_venda = 'Normal'""",
              v=(novo == "sim"), c=cenario_id)
        elif k == "preco_investidor_unidade":
            q(s, """INSERT INTO preco_cenario (cenario_id, tipo_venda, preco_unidade,
                                               usar_tabela)
                    VALUES (:c,'Investidor',:v,false)
                    ON CONFLICT (cenario_id, tipo_venda)
                    DO UPDATE SET preco_unidade = :v""", c=cenario_id, v=novo)

        registrar(s, emp_id=emp_id, cenario_id=cenario_id, modulo="comercial",
                  entidade="tabela_venda" if k in COLUNA_DA_TABELA else "preco_cenario",
                  campo=k, antes=antes, depois=novo, autor=autor)
        mudou.append(k)

    return mudou


# =====================================================================
# leitura e gravação do cadastro
# =====================================================================
def ler_cadastro(s: Session, emp_id: int) -> dict:
    e = q(s, "SELECT * FROM empreendimento WHERE id = :e", e=emp_id)[0]
    return {c.chave: e.get(c.chave) for c in CADASTRO.campos}


def gravar_cadastro(s: Session, *, emp_id: int, enviado: dict,
                    autor: str) -> list[str]:
    atual = ler_cadastro(s, emp_id)
    mudou: list[str] = []
    for campo in CADASTRO.campos:
        k = campo.chave
        novo = campo.ler(enviado.get(k))
        if campo.tipo in ("numero",) and novo == 0 and atual.get(k) is None:
            continue
        if campo.tipo == "inteiro" and novo == 0:
            novo = None
        if not diferentes(atual.get(k), novo):
            continue
        q(s, f"UPDATE empreendimento SET {k} = :v WHERE id = :e", v=novo, e=emp_id)
        registrar(s, emp_id=emp_id, cenario_id=None, modulo="cadastro",
                  entidade="empreendimento", campo=k,
                  antes=atual.get(k), depois=novo, autor=autor)
        mudou.append(k)
    return mudou


LEITORES: dict[str, Callable] = {
    "cadastro": lambda s, emp, cen: ler_cadastro(s, emp),
    "premissas": lambda s, emp, cen: ler_premissas(s, cen),
    "comercial": lambda s, emp, cen: ler_comercial(s, emp, cen),
}

GRAVADORES: dict[str, Callable] = {
    "cadastro": lambda s, emp, cen, env, autor: gravar_cadastro(
        s, emp_id=emp, enviado=env, autor=autor),
    "premissas": lambda s, emp, cen, env, autor: gravar_premissas(
        s, emp_id=emp, cenario_id=cen, enviado=env, autor=autor),
    "comercial": lambda s, emp, cen, env, autor: gravar_comercial(
        s, emp_id=emp, cenario_id=cen, enviado=env, autor=autor),
}


# =====================================================================
# módulos de lista: plano de vendas, obra, terreno, unidades
# =====================================================================
def ler_plano(s: Session, cenario_id: int) -> list[dict]:
    return q(s, """SELECT mes, tipo_venda, quantidade FROM plano_venda
                    WHERE cenario_id = :c ORDER BY mes, tipo_venda""", c=cenario_id)


def gravar_plano(s: Session, *, emp_id: int, cenario_id: int,
                 linhas: list[tuple], autor: str) -> list[str]:
    """
    `linhas` são (mes, tipo_venda, quantidade). O plano é substituído inteiro:
    é uma lista, não um conjunto de campos, e comparar linha a linha para
    descobrir que a de março saiu daria um histórico pior, não melhor.
    """
    antes = {(l["mes"], l["tipo_venda"]): int(l["quantidade"])
             for l in ler_plano(s, cenario_id)}
    depois = {(m, t): quantidade for m, t, quantidade in linhas if m and quantidade > 0}

    mudou = []
    for chave in sorted(set(antes) | set(depois)):
        a, d = antes.get(chave), depois.get(chave)
        if a == d:
            continue
        mes, tipo = chave
        registrar(s, emp_id=emp_id, cenario_id=cenario_id, modulo="plano",
                  entidade=f"plano:{mes:%m/%Y}:{tipo}", campo="quantidade",
                  antes=a, depois=d, autor=autor)
        mudou.append(f"{mes:%m/%Y}")

    if mudou:
        q(s, "DELETE FROM plano_venda WHERE cenario_id = :c", c=cenario_id)
        for (mes, tipo), quantidade in sorted(depois.items()):
            q(s, """INSERT INTO plano_venda (cenario_id, mes, tipo_venda, quantidade)
                    VALUES (:c,:m,:t,:q)""", c=cenario_id, m=mes, t=tipo, q=quantidade)
    return mudou


def ler_terreno(s: Session, cenario_id: int) -> list[dict]:
    return q(s, """SELECT ordem, valor, vencimento FROM premissa_terreno
                    WHERE cenario_id = :c ORDER BY ordem""", c=cenario_id)


def gravar_terreno(s: Session, *, emp_id: int, cenario_id: int,
                   parcelas: list[tuple], autor: str) -> list[str]:
    """
    Compara por **posição**, não pelo `ordem` gravado.

    A carga inicial numera a partir de zero e esta tela numera a partir de um;
    comparar pelo número guardado fazia a lista inteira parecer deslocada — a
    primeira parcela sumia, a segunda virava a primeira, e o histórico
    registrava cinco alterações onde houve uma. Aqui a primeira parcela é
    comparada com a primeira, e a numeração é normalizada na gravação.
    """
    antes = [(float(l["valor"]), l["vencimento"]) for l in ler_terreno(s, cenario_id)]
    depois = [(v, d) for v, d in parcelas if v > 0]

    mudou = []
    for i in range(max(len(antes), len(depois))):
        a = antes[i] if i < len(antes) else None
        d = depois[i] if i < len(depois) else None
        if a == d:
            continue
        registrar(s, emp_id=emp_id, cenario_id=cenario_id, modulo="terreno",
                  entidade=f"parcela {i + 1}", campo="valor e vencimento",
                  antes=_parcela_texto(a), depois=_parcela_texto(d), autor=autor)
        mudou.append(f"parcela {i + 1}")

    if mudou:
        q(s, "DELETE FROM premissa_terreno WHERE cenario_id = :c", c=cenario_id)
        for ordem, (valor, venc) in enumerate(depois, start=1):
            q(s, """INSERT INTO premissa_terreno (cenario_id, ordem, valor, vencimento)
                    VALUES (:c,:o,:v,:d)""", c=cenario_id, o=ordem, v=valor, d=venc)
    return mudou


def _parcela_texto(p) -> Optional[str]:
    if not p:
        return None
    valor, venc = p
    return f"R$ {valor:,.2f}".replace(",", " ") + (f" em {venc:%m/%Y}" if venc else "")


def ler_obra(s: Session, emp_id: int) -> dict:
    """
    Orçamentos do empreendimento e a curva do vigente, já agregada.

    A curva mostrada é a do orçamento inteiro: cada item da EAP tem a sua, e o
    desembolso de um mês é a soma ponderada pelo peso de cada item. Mostrar a
    curva de um item só seria mostrar uma fração e chamá-la de curva.
    """
    orcs = q(s, """SELECT * FROM orcamento_obra WHERE empreendimento_id = :e
                    ORDER BY vigente DESC, id DESC""", e=emp_id)
    vigente = next((o for o in orcs if o["vigente"]), orcs[0] if orcs else None)
    curva, itens = [], 0
    if vigente:
        itens = q(s, "SELECT count(*) AS n FROM eap_item WHERE orcamento_id = :o",
                  o=vigente["id"])[0]["n"]
        curva = q(s, """
            SELECT ci.mes, sum(ci.perc_fisico * ei.peso) AS perc_fisico
              FROM cronograma_item ci
              JOIN eap_item ei ON ei.id = ci.eap_item_id
             WHERE ei.orcamento_id = :o
             GROUP BY ci.mes ORDER BY ci.mes""", o=vigente["id"])
    return {"orcamentos": orcs, "vigente": vigente, "curva": curva,
            "itens_eap": itens}


def gravar_obra(s: Session, *, emp_id: int, orcamento_id: int, custo_raso: float,
                versao: str, curva: list[tuple], autor: str,
                substituir_eap: bool = False) -> list[str]:
    """
    `curva` são (mes, fracao) e a soma tem de fechar 1 — a trigger confere.

    Gravar uma curva agregada num orçamento que tem EAP detalhada é destrutivo:
    a curva agregada não sabe reconstruir os 21 itens de onde veio. Por isso
    `substituir_eap` precisa vir explícito da tela; sem ele, um orçamento com
    mais de um item recusa a gravação da curva e o resto (custo, versão) passa.
    """
    atual = q(s, "SELECT * FROM orcamento_obra WHERE id = :o", o=orcamento_id)[0]
    mudou = []

    if diferentes(float(atual["custo_raso"]), custo_raso) and custo_raso > 0:
        registrar(s, emp_id=emp_id, cenario_id=None, modulo="obra",
                  entidade=f"orçamento {atual['versao']}", campo="custo_raso",
                  antes=float(atual["custo_raso"]), depois=custo_raso, autor=autor)
        q(s, "UPDATE orcamento_obra SET custo_raso = :v WHERE id = :o",
          v=custo_raso, o=orcamento_id)
        mudou.append("custo raso")

    if versao and versao != atual["versao"]:
        registrar(s, emp_id=emp_id, cenario_id=None, modulo="obra",
                  entidade="orçamento", campo="versao",
                  antes=atual["versao"], depois=versao, autor=autor)
        q(s, "UPDATE orcamento_obra SET versao = :v WHERE id = :o",
          v=versao, o=orcamento_id)
        mudou.append("versão")

    if not curva:
        return mudou

    itens = q(s, "SELECT id, codigo, peso FROM eap_item WHERE orcamento_id = :o "
                 "ORDER BY codigo", o=orcamento_id)
    if len(itens) > 1 and not substituir_eap:
        raise ValueError(
            f"este orçamento tem {len(itens)} itens de EAP, cada um com a sua "
            f"curva. Gravar a curva agregada apagaria essa estrutura, e ela não "
            f"se reconstrói a partir do agregado. Marque a substituição na tela "
            f"se for mesmo para trocar a EAP detalhada por um item único.")

    antiga = {l["mes"]: float(l["perc_fisico"]) for l in q(s, """
        SELECT ci.mes, sum(ci.perc_fisico * ei.peso) AS perc_fisico
          FROM cronograma_item ci JOIN eap_item ei ON ei.id = ci.eap_item_id
         WHERE ei.orcamento_id = :o GROUP BY ci.mes""", o=orcamento_id)}
    nova = {m: f for m, f in curva if m}
    if antiga == nova:
        return mudou

    registrar(s, emp_id=emp_id, cenario_id=None, modulo="obra",
              entidade=f"curva do orçamento {atual['versao']}", campo="cronograma",
              antes=f"{len(antiga)} meses em {len(itens)} item(ns) de EAP",
              depois=f"{len(nova)} meses em 1 item", autor=autor)

    # a curva agregada vira um item único: manter os 21 antigos com a mesma
    # curva seria fingir um detalhamento que já não existe
    q(s, "DELETE FROM eap_item WHERE orcamento_id = :o", o=orcamento_id)
    item_id = q(s, """INSERT INTO eap_item (orcamento_id, codigo, descricao, peso)
                      VALUES (:o,'01','OBRA — curva agregada, sem EAP detalhada',1.0)
                      RETURNING id""", o=orcamento_id)[0]["id"]
    for mes, fracao in sorted(nova.items()):
        q(s, """INSERT INTO cronograma_item (eap_item_id, mes, perc_fisico)
                VALUES (:i,:m,:p)""", i=item_id, m=mes, p=fracao)
    mudou.append("curva física")
    return mudou


def ler_unidades(s: Session, emp_id: int) -> list[dict]:
    return q(s, """
        SELECT u.id, u.nome, u.tipo_imovel, u.area_privativa, u.situacao,
               u.tipo_venda, u.considerar_na_viabilidade, u.motivo_exclusao,
               u.origem_cadastro,
               (SELECT p.preco_bruto FROM preco_unidade p
                 WHERE p.unidade_id = u.id
                 ORDER BY p.vigente_desde DESC LIMIT 1) AS preco_bruto
          FROM unidade u
         WHERE u.empreendimento_id = :e
         ORDER BY u.nome""", e=emp_id)


CAMPOS_UNIDADE = ("area_privativa", "situacao", "tipo_venda",
                  "considerar_na_viabilidade", "motivo_exclusao")


def gravar_unidade(s: Session, *, emp_id: int, unidade_id: int, enviado: dict,
                   autor: str) -> list[str]:
    atual = q(s, """SELECT u.*, (SELECT p.preco_bruto FROM preco_unidade p
                                  WHERE p.unidade_id = u.id
                                  ORDER BY p.vigente_desde DESC LIMIT 1) AS preco_bruto
                      FROM unidade u WHERE u.id = :u AND u.empreendimento_id = :e""",
              u=unidade_id, e=emp_id)
    if not atual:
        raise ValueError("unidade não é deste empreendimento")
    atual = atual[0]
    mudou = []

    novos = {
        "area_privativa": num(enviado.get("area_privativa")),
        "situacao": texto(enviado.get("situacao")),
        "tipo_venda": texto(enviado.get("tipo_venda")),
        "considerar_na_viabilidade": enviado.get("considerar") == "1",
        "motivo_exclusao": texto(enviado.get("motivo_exclusao")) or None,
    }
    for campo, novo in novos.items():
        antes = atual[campo]
        if isinstance(antes, (int, float)) and not isinstance(antes, bool):
            antes = float(antes)
        if not diferentes(antes, novo):
            continue
        q(s, f"UPDATE unidade SET {campo} = :v WHERE id = :u", v=novo, u=unidade_id)
        registrar(s, emp_id=emp_id, cenario_id=None, modulo="unidades",
                  entidade=f"unidade {atual['nome']}", campo=campo,
                  antes=antes, depois=novo, autor=autor)
        mudou.append(campo)

    preco = num(enviado.get("preco_bruto"))
    anterior = float(atual["preco_bruto"] or 0)
    if preco > 0 and diferentes(anterior, preco):
        # preço é versionado: entra uma linha nova, a antiga fica no histórico
        q(s, """INSERT INTO preco_unidade (unidade_id, preco_bruto, vigente_desde,
                                           observacao)
                VALUES (:u,:p,CURRENT_DATE,'editado na tela')
                ON CONFLICT (unidade_id, vigente_desde)
                DO UPDATE SET preco_bruto = :p, observacao = 'editado na tela'""",
          u=unidade_id, p=preco)
        registrar(s, emp_id=emp_id, cenario_id=None, modulo="unidades",
                  entidade=f"unidade {atual['nome']}", campo="preco_bruto",
                  antes=anterior, depois=preco, autor=autor)
        mudou.append("preço")

    return mudou


def historico(s: Session, emp_id: int, limite: int = 200) -> list[dict]:
    return q(s, """
        SELECT a.*, c.nome AS cenario
          FROM alteracao a
          LEFT JOIN cenario c ON c.id = a.cenario_id
         WHERE a.empreendimento_id = :e
         ORDER BY a.em DESC, a.id DESC
         LIMIT :n""", e=emp_id, n=limite)


# =====================================================================
# composição de um setor de custo
# =====================================================================
def setores(s: Session) -> list[dict]:
    return q(s, "SELECT * FROM setor_custo ORDER BY ordem")


def setor(s: Session, codigo: str) -> Optional[dict]:
    r = q(s, "SELECT * FROM setor_custo WHERE codigo = :c", c=codigo)
    return r[0] if r else None


def ler_composicao(s: Session, cenario_id: int, codigo: str) -> list[dict]:
    return q(s, """SELECT * FROM composicao_item
                    WHERE cenario_id = :c AND setor = :s
                    ORDER BY ordem, id""", c=cenario_id, s=codigo)


def totais_por_setor(s: Session, cenario_id: int) -> dict[str, dict]:
    linhas = q(s, """SELECT setor, count(*) AS n, sum(valor) AS total
                       FROM composicao_item WHERE cenario_id = :c
                      GROUP BY setor""", c=cenario_id)
    return {l["setor"]: {"n": l["n"], "total": float(l["total"])} for l in linhas}


def gravar_composicao(s: Session, *, emp_id: int, cenario_id: int, codigo: str,
                      itens: list[dict], autor: str) -> list[str]:
    """
    Substitui a composição inteira do setor.

    Compara por **posição**, como o terreno: a composição é uma lista, e
    comparar pelo id faria remover a segunda linha parecer "a segunda mudou, a
    terceira mudou, a quarta sumiu". O histórico registra item a item.
    """
    antes = [(i["descricao"], float(i["valor"])) for i in
             ler_composicao(s, cenario_id, codigo)]
    depois = [(i["descricao"], i["valor"]) for i in itens
              if i["descricao"].strip() and i["valor"] > 0]

    mudou = []
    for i in range(max(len(antes), len(depois))):
        a = antes[i] if i < len(antes) else None
        d = depois[i] if i < len(depois) else None
        if a == d:
            continue
        registrar(s, emp_id=emp_id, cenario_id=cenario_id, modulo="custos",
                  entidade=f"{codigo} · item {i + 1}", campo="descrição e valor",
                  antes=_item_texto(a), depois=_item_texto(d), autor=autor)
        mudou.append(f"item {i + 1}")

    if not mudou:
        return []

    q(s, """DELETE FROM composicao_item WHERE cenario_id = :c AND setor = :s""",
      c=cenario_id, s=codigo)
    for ordem, item in enumerate(
            [i for i in itens if i["descricao"].strip() and i["valor"] > 0], start=1):
        q(s, """INSERT INTO composicao_item
                  (cenario_id, setor, ordem, descricao, quantidade, unidade,
                   valor_unitario, valor, observacao)
                VALUES (:c,:s,:o,:d,:q,:u,:vu,:v,:ob)""",
          c=cenario_id, s=codigo, o=ordem, d=item["descricao"].strip(),
          q=item.get("quantidade") or None, u=(item.get("unidade") or "").strip() or None,
          vu=item.get("valor_unitario") or None, v=item["valor"],
          ob=(item.get("observacao") or "").strip() or None)
    return mudou


def _item_texto(item) -> Optional[str]:
    if not item:
        return None
    descricao, valor = item
    return f"{descricao} — R$ {moeda(valor)}"
