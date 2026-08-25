"""
Modelo de dados do motor de viabilidade de incorporação.

Traduz a planilha "Incorrido SPE Kiev" em entidades explícitas.
Cada dataclass aqui corresponde a uma tabela do banco (ver schema.sql).

Convenção de sinais (a mesma da planilha):
  receitas  > 0
  gastos    < 0
Tudo em R$ nominais (sem correção monetária), competência = caixa.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Optional


# --------------------------------------------------------------------------
# 1. CADASTRO DO EMPREENDIMENTO
# --------------------------------------------------------------------------

class SituacaoUnidade(str, Enum):
    """Comercial!G — vem de Unidades!L (Sienge commercialStock)."""
    VENDIDA = "Vendida"
    DISPONIVEL = "Disponível"
    PERMUTA = "Permuta"
    RESERVADA = "Reservada"


class TipoVenda(str, Enum):
    """Comercial!J — classificação comercial interna, define a tabela de preço."""
    NORMAL = "Normal"        # tabela padrão
    INVESTIDOR = "Investidor"  # lote de unidades vendido a investidor, prazo curto
    LEAL = "Leal"            # preço de tabela reduzido (venda para o grupo)
    GARAGEM = "Garagem"


@dataclass
class Unidade:
    """Comercial linhas 6..205 (1 linha = 1 unidade autônoma)."""
    unidade: str                 # Comercial!B  — Unidades.name (Sienge)
    area_privativa: float        # Comercial!C  — Unidades.privateArea
    valor_bruto: float           # Comercial!D  = comissão + líquido
    comissao: float              # Comercial!E  — comissão embutida no preço
    valor_liquido: float         # Comercial!F  — Contratos.value quando vendida
    situacao: SituacaoUnidade    # Comercial!G
    tipo_venda: TipoVenda        # Comercial!J
    data_contrato: Optional[date] = None   # Comercial!H — Contratos.contractDate

    # --- realizado / carteira (vem do Sienge, não é premissa) ---
    recebido: float = 0.0                 # Comercial!N  = Σ Recebido por unidade
    a_receber_poupanca: float = 0.0       # Comercial!CB = Σ Receber, condição 1 (mensal)
    a_receber_pos_chaves: float = 0.0     # Comercial!CF = Σ Receber, condição 2 e 3
    permuta_valor: float = 0.0            # Comercial!CP = valor_liquido se situação=Permuta

    @property
    def preco_m2(self) -> float:
        """Comercial!A = D/C"""
        return self.valor_bruto / self.area_privativa if self.area_privativa else 0.0

    @property
    def a_receber_total(self) -> float:
        """Comercial!CH"""
        return self.a_receber_poupanca + self.a_receber_pos_chaves

    def estoque(self, preco_m2_cenario: float, usar_tabela: bool) -> float:
        """
        Comercial!CJ/CL: unidade não vendida entra no VGV a 50% poupança + 50% pós-chaves.
        `usar_tabela=True` usa o preço de tabela cadastrado (cenário realista);
        senão precifica pela área × preço/m² do cenário (otimista/pessimista).
        """
        if self.situacao in (SituacaoUnidade.VENDIDA, SituacaoUnidade.PERMUTA):
            return 0.0
        return (self.valor_liquido if usar_tabela
                else self.area_privativa * preco_m2_cenario)


# --------------------------------------------------------------------------
# 2. PREMISSAS COMERCIAIS (tabela de venda)
# --------------------------------------------------------------------------

@dataclass
class TabelaVenda:
    """
    SIMULAÇÕES linhas 70..79 — como o preço da unidade se decompõe em parcelas.
    Os percentuais são sobre o VALOR BRUTO; a comissão sai do bruto e o restante
    (`valor liquido comissão`) é o que a SPE recebe.
    A soma comissão+ato+mensais+anuais+semestrais+única+chaves = 100%.
    """
    comissao: float = 0.06        # F71 — % sobre o bruto, paga ao corretor
    ato: float = 0.04             # F72 — entrada no ato do contrato
    mensais: float = 0.35         # F73 — parcelas mensais até as chaves
    anuais: float = 0.00          # F74
    semestrais: float = 0.35      # F75 — balões semestrais
    unica: float = 0.00           # F76
    chaves: float = 0.20          # F77 — financiamento/repasse na entrega

    # prazos (Vendas_*!J) — nº de parcelas de cada componente
    n_mensais: int = 60           # Vendas!J13
    n_ato: int = 1
    n_chaves: int = 1

    def valida(self) -> None:
        s = (self.comissao + self.ato + self.mensais + self.anuais
             + self.semestrais + self.unica + self.chaves)
        assert abs(s - 1.0) < 1e-6, f"tabela de venda soma {s:.6f}, deveria somar 1"

    @property
    def perc_liquidos(self) -> dict[str, float]:
        """
        Vendas_*!H — os percentuais são renormalizados sobre o valor LÍQUIDO de
        comissão (SIMULAÇÕES H72..H77 = G72../G78), que é a base do fluxo da SPE.
        """
        base = 1.0 - self.comissao
        return {
            "ato": self.ato / base,
            "mensal": self.mensais / base,
            "anual": self.anuais / base,
            "semestral": self.semestrais / base,
            "chaves": self.chaves / base,
        }


@dataclass
class PlanoVendaMes:
    """SIMULAÇÕES linhas 82..157 — velocidade de vendas por mês e por cenário."""
    mes: date
    quantidade: int
    tipo: TipoVenda = TipoVenda.NORMAL


# --------------------------------------------------------------------------
# 3. PREMISSAS DE OBRA
# --------------------------------------------------------------------------

@dataclass
class AtividadeObra:
    """
    Cronograma obra linhas 4..56.
    `curva[mes]` é o % FÍSICO da atividade executado naquele mês (soma = 1).
    """
    item: str
    atividade: str
    valor: float
    curva: dict[date, float] = field(default_factory=dict)

    def desembolso(self) -> dict[date, float]:
        return {m: self.valor * p for m, p in self.curva.items()}


@dataclass
class Obra:
    """
    Custo raso da obra. Na planilha o valor total (SIMULAÇÕES M36) é o
    custo/m² × área privativa; a distribuição no tempo vem do cronograma.
    """
    custo_raso: float                       # negativo
    area_privativa: float
    area_construida: float
    atividades: list[AtividadeObra] = field(default_factory=list)
    # fallback: curva agregada pronta (VM linha 205), quando não há EAP detalhada
    curva_agregada: dict[date, float] = field(default_factory=dict)

    @property
    def custo_m2_privativa(self) -> float:
        """SIMULAÇÕES F36 = custo_raso / área privativa."""
        return abs(self.custo_raso) / self.area_privativa

    @property
    def eficiencia(self) -> float:
        """SIMULAÇÕES F59 = privativa / construída."""
        return self.area_privativa / self.area_construida


@dataclass
class ItemEAPCusto:
    """
    Custo_simulação linhas 5..25 — a EAP de 21 macro-itens usada para
    simular o efeito de renegociações sobre o custo raso.
    """
    codigo: str
    descricao: str
    peso: float          # F — % do custo total (Σ = 1)
    variacao: float = 0  # G — ganho(-)/perda(+) negociado sobre o item

    def valor(self, custo_total: float) -> float:
        return custo_total * self.peso           # E

    def valor_ajustado(self, custo_total: float) -> float:
        return self.valor(custo_total) * (1 + self.variacao)   # H


# --------------------------------------------------------------------------
# 4. PREMISSAS FINANCEIRAS E DE DESPESA
# --------------------------------------------------------------------------

@dataclass
class Premissas:
    """
    Um cenário completo. Os defaults são os do cenário REALISTA da Kiev.
    Percentuais sempre positivos; o motor aplica o sinal.
    """
    nome: str = "realista"

    # --- preço ---
    preco_m2_estoque: float = 18_228.66      # SIMULAÇÕES L11/L17
    usar_preco_tabela: bool = True           # realista usa a tabela cadastrada
    preco_investidor_unidade: float = 3_704_512.50   # M79

    # --- deduções da receita ---
    ret: float = 0.045                       # L26 — RET/patrimônio de afetação
    distratos: float = 0.0                   # L27
    despesas_comerciais: float = 30_750.0    # M28 — valor fixo

    # --- terreno ---
    terreno_parcelas: list[float] = field(
        default_factory=lambda: [2_200_000, 350_000, 350_000, 300_000, 250_000])  # M34
    terreno_registro_perc: float = 0.025     # L35 — sobre o pagamento em dinheiro

    # --- taxas do incorporador ---
    taxa_adm_obra: float = 0.10              # L37 — sobre o custo raso
    taxa_viabilizacao: float = 0.05          # L38 — sobre a receita SPE (taxa de carteira)

    # --- despesas de incorporação/marketing ---
    decoracao: float = 1_545_045.14          # M40
    projetos_e_outros: float = 2_899_156.99  # M41
    marketing_stand: float = 0.0             # M42
    marketing_propaganda: float = 1_545_045.14  # M43
    outras_desp_adm_perc: float = 0.015      # L44 — sobre o custo raso
    outras_entradas: float = 117.95          # M45

    # --- financiamento à produção ---
    financiamento_limite: float = 0.0        # VM D199
    financiamento_juros_aa: float = 0.22     # VM D195
    financiamento_prazo_amort: int = 6       # VM E199
    financiamento_gatilho_obra: float = 0.20 # VM: libera quando evolução ≥ 20%

    # --- correção monetária da carteira ---
    # A planilha projeta em valores nominais. Aqui a correção é opcional e
    # explícita: enquanto os índices forem None, o resultado é idêntico ao dela.
    indice_ate_chaves: Optional[str] = None    # 'INCC-DI' durante a obra
    indice_apos_chaves: Optional[str] = None   # 'IGP-M' ou 'IPCA' no repasse
    indice_projetado_aa: float = 0.0           # taxa usada nos meses sem série
    serie_indice: dict = field(default_factory=dict)  # {código: {mês: variação}}
    # Corrigir só a carteira e deixar a obra em moeda de hoje inventa lucro:
    # o INCC que reajusta a parcela do cliente é o mesmo que encarece o
    # concreto. Ligado por padrão sempre que houver índice até as chaves.
    corrigir_custo_obra: bool = True

    # --- indicadores ---
    tma_anual: float = 0.18                  # L60

    # --- calendário ---
    mes_base: date = date(2026, 8, 31)       # 1º mês projetado (VM col AF)
    meses_pos_chaves: int = 6                # VM E22/E24 — diluição do saldo final

    @property
    def tma_mensal(self) -> float:
        """L61 = (1+TMA)^(1/12) − 1"""
        return (1 + self.tma_anual) ** (1 / 12) - 1

    @property
    def juros_mensal(self) -> float:
        return (1 + self.financiamento_juros_aa) ** (1 / 12) - 1

    @property
    def indice_projetado_mensal(self) -> float:
        return (1 + self.indice_projetado_aa) ** (1 / 12) - 1

    def variacao_do_mes(self, codigo: Optional[str], mes: date) -> float:
        """
        Série histórica quando existe; taxa projetada quando não existe.
        Sem índice configurado, zero — que é a projeção nominal da planilha.
        """
        if not codigo:
            return 0.0
        historico = self.serie_indice.get(codigo, {})
        if mes in historico:
            return float(historico[mes])
        return self.indice_projetado_mensal

    @property
    def corrige_carteira(self) -> bool:
        return bool(self.indice_ate_chaves or self.indice_apos_chaves)
