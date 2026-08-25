-- =====================================================================
-- Modelo relacional do sistema de viabilidade de incorporação
-- Derivado da planilha "Incorrido SPE Kiev"  ·  PostgreSQL 15+
--
-- Princípio de projeto que a planilha viola e o sistema deve respeitar:
--   PREMISSA (o que a gente escolhe)  ≠  REALIZADO (o que o Sienge diz)
--   ≠  PROJEÇÃO (o que o motor calcula).
-- As três coisas moram em tabelas separadas e nunca se sobrescrevem.
-- =====================================================================

CREATE SCHEMA IF NOT EXISTS viab;
SET search_path = viab, public;

-- ---------------------------------------------------------------------
-- 1. EMPREENDIMENTO
-- ---------------------------------------------------------------------
CREATE TABLE empreendimento (
    id                  bigserial PRIMARY KEY,
    sienge_enterprise_id integer UNIQUE,          -- Unidades.enterpriseId
    sienge_company_id   integer,                  -- Fin_Obra.companyId    (26)
    nome                text NOT NULL,            -- razão social da SPE
    cnpj                text,
    area_construida     numeric(14,2) NOT NULL,   -- SIMULAÇÕES L57
    area_privativa      numeric(14,2) NOT NULL,   -- L58 = Σ áreas das unidades
    data_lancamento     date,
    data_entrega_prevista date,                   -- define o mês da parcela "chaves"
    criado_em           timestamptz NOT NULL DEFAULT now(),
    CHECK (area_construida > 0 AND area_privativa > 0)
);
COMMENT ON COLUMN empreendimento.area_privativa IS
    'Eficiência = area_privativa/area_construida (SIMULAÇÕES L59)';

-- ---------------------------------------------------------------------
-- 2. UNIDADES E CARTEIRA          (aba Comercial / Unidades / Contratos)
-- ---------------------------------------------------------------------
CREATE TYPE situacao_unidade AS ENUM ('Disponível','Reservada','Vendida','Permuta','Distratada');
CREATE TYPE tipo_venda       AS ENUM ('Normal','Investidor','Leal','Garagem');

CREATE TABLE unidade (
    id                bigserial PRIMARY KEY,
    empreendimento_id bigint NOT NULL REFERENCES empreendimento ON DELETE CASCADE,
    sienge_unit_id    integer,                    -- Unidades.id
    nome              text NOT NULL,              -- Unidades.name  ('601')
    tipo_imovel       text,                       -- 'SALA COMERCIAL'
    area_privativa    numeric(12,2) NOT NULL,
    fracao_ideal      numeric(12,8),
    situacao          situacao_unidade NOT NULL DEFAULT 'Disponível',
    tipo_venda        tipo_venda       NOT NULL DEFAULT 'Normal',
    -- preço de tabela: é PREMISSA, versionada em tabela_preco_item
    UNIQUE (empreendimento_id, nome)
);
CREATE INDEX ON unidade (empreendimento_id, situacao);

-- fim do mês: usada pelas colunas de competência. Marcada IMMUTABLE de
-- propósito — sem isso o Postgres recusa a expressão numa coluna gerada.
CREATE OR REPLACE FUNCTION fim_do_mes(d date) RETURNS date AS $$
    SELECT (date_trunc('month', $1::timestamp) + interval '1 month' - interval '1 day')::date
$$ LANGUAGE sql IMMUTABLE STRICT;

CREATE TABLE contrato (
    id                bigserial PRIMARY KEY,
    unidade_id        bigint NOT NULL REFERENCES unidade ON DELETE CASCADE,
    sienge_contract_id integer UNIQUE,            -- Contratos.Column1.id
    sienge_bill_id    integer,                    -- Contratos.receivableBillId
    cliente_nome      text,
    data_contrato     date NOT NULL,              -- Contratos.contractDate
    valor_bruto       numeric(16,2) NOT NULL,     -- Comercial!D
    comissao          numeric(16,2) NOT NULL,     -- Comercial!E (embutida no bruto)
    valor_liquido     numeric(16,2) GENERATED ALWAYS AS (valor_bruto - comissao) STORED,
    indexador         text,                       -- Contratos.correctionType (INCC/IGPM)
    situacao          text                        -- Contratos.situation
);

-- ---------------------------------------------------------------------
-- 3. TABELA DE VENDA (premissa comercial)   (SIMULAÇÕES 70..79)
-- ---------------------------------------------------------------------
CREATE TABLE tabela_venda (
    id                bigserial PRIMARY KEY,
    empreendimento_id bigint NOT NULL REFERENCES empreendimento ON DELETE CASCADE,
    nome              text NOT NULL,              -- 'Padrão' | 'Investidor'
    perc_comissao     numeric(7,6) NOT NULL,      -- 0.060000
    perc_ato          numeric(7,6) NOT NULL,
    perc_mensais      numeric(7,6) NOT NULL,
    perc_anuais       numeric(7,6) NOT NULL DEFAULT 0,
    perc_semestrais   numeric(7,6) NOT NULL,
    perc_unica        numeric(7,6) NOT NULL DEFAULT 0,
    perc_chaves       numeric(7,6) NOT NULL,
    qtd_mensais       smallint NOT NULL,          -- Vendas!J13 (60)
    UNIQUE (empreendimento_id, nome),
    -- a trava que a planilha não tem: os percentuais TÊM de fechar 100%
    CONSTRAINT tabela_fecha_100 CHECK (
        round(perc_comissao + perc_ato + perc_mensais + perc_anuais
              + perc_semestrais + perc_unica + perc_chaves, 6) = 1.000000)
);

-- ---------------------------------------------------------------------
-- 4. CENÁRIO E PREMISSAS                    (aba SIMULAÇÕES, 3 blocos)
-- ---------------------------------------------------------------------
CREATE TABLE cenario (
    id                bigserial PRIMARY KEY,
    empreendimento_id bigint NOT NULL REFERENCES empreendimento ON DELETE CASCADE,
    nome              text NOT NULL,              -- 'otimista'|'realista'|'pessimista'
    tipo              text NOT NULL DEFAULT 'projecao'
                      CHECK (tipo IN ('orcado','projecao')),  -- 'orcado' = congelado
    congelado_em      timestamptz,                -- preenchido quando vira 'orcado'
    mes_base          date NOT NULL,              -- 1º mês projetado (VM col AF)
    horizonte_meses   smallint NOT NULL DEFAULT 90,
    UNIQUE (empreendimento_id, nome, tipo)
);

CREATE TABLE premissa (
    cenario_id        bigint NOT NULL REFERENCES cenario ON DELETE CASCADE,
    chave             text   NOT NULL,   -- 'ret','taxa_adm_obra','tma_anual', ...
    valor             numeric(18,8) NOT NULL,
    unidade           text NOT NULL CHECK (unidade IN ('percentual','moeda','meses','r$/m2')),
    origem            text,              -- referência à célula da planilha
    PRIMARY KEY (cenario_id, chave)
);
COMMENT ON TABLE premissa IS
 'Chave/valor em vez de 40 colunas: premissas nascem e morrem sem migração.
  Chaves esperadas: preco_m2_estoque, ret, distratos, despesas_comerciais,
  terreno_registro_perc, taxa_adm_obra, taxa_viabilizacao, decoracao,
  projetos_e_outros, marketing_stand, marketing_propaganda,
  outras_desp_adm_perc, outras_entradas, tma_anual, financiamento_limite,
  financiamento_juros_aa, financiamento_prazo_amort, meses_pos_chaves.';

-- preço do estoque por cenário (SIMULAÇÕES F11/L11/R11 e F13/L13/R13)
CREATE TABLE preco_cenario (
    cenario_id   bigint NOT NULL REFERENCES cenario ON DELETE CASCADE,
    tipo_venda   tipo_venda NOT NULL,
    preco_m2     numeric(14,4),                   -- usado quando usar_tabela = false
    preco_unidade numeric(16,2),                  -- lote fechado (investidor)
    usar_tabela  boolean NOT NULL DEFAULT false,  -- realista usa o preço cadastrado
    PRIMARY KEY (cenario_id, tipo_venda)
);

-- velocidade de vendas (SIMULAÇÕES 82..157)
CREATE TABLE plano_venda (
    cenario_id   bigint NOT NULL REFERENCES cenario ON DELETE CASCADE,
    mes          date   NOT NULL,                 -- sempre último dia do mês
    tipo_venda   tipo_venda NOT NULL DEFAULT 'Normal',
    quantidade   smallint NOT NULL CHECK (quantidade >= 0),
    PRIMARY KEY (cenario_id, mes, tipo_venda)
);

-- ---------------------------------------------------------------------
-- 5. OBRA                        (Custo_simulação + Cronograma obra)
-- ---------------------------------------------------------------------
CREATE TABLE orcamento_obra (
    id                bigserial PRIMARY KEY,
    empreendimento_id bigint NOT NULL REFERENCES empreendimento ON DELETE CASCADE,
    versao            text NOT NULL,              -- 'preliminar','executivo',...
    custo_raso        numeric(16,2) NOT NULL,     -- SIMULAÇÕES M36 (positivo aqui)
    data_base         date NOT NULL,
    indice_reajuste   text,                       -- 'INCC-DI', 'CUB-PA'
    vigente           boolean NOT NULL DEFAULT false,
    UNIQUE (empreendimento_id, versao)
);
-- só um orçamento vigente por empreendimento
CREATE UNIQUE INDEX ON orcamento_obra (empreendimento_id) WHERE vigente;

CREATE TABLE eap_item (
    id              bigserial PRIMARY KEY,
    orcamento_id    bigint NOT NULL REFERENCES orcamento_obra ON DELETE CASCADE,
    codigo          text NOT NULL,                -- '01'..'21'
    descricao       text NOT NULL,                -- 'FUNDAÇÕES E CONTENÇÕES'
    peso            numeric(9,8) NOT NULL,        -- Custo_simulação F  (Σ = 1)
    variacao_negociada numeric(7,6) NOT NULL DEFAULT 0,  -- Custo_simulação G
    UNIQUE (orcamento_id, codigo)
);
COMMENT ON COLUMN eap_item.variacao_negociada IS
 'Ganho(-)/perda(+) de negociação por item. valor_ajustado = custo_raso*peso*(1+variacao)';

-- curva física: % da atividade executado em cada mês (Cronograma obra H..BS)
CREATE TABLE cronograma_item (
    eap_item_id   bigint NOT NULL REFERENCES eap_item ON DELETE CASCADE,
    mes           date   NOT NULL,
    perc_fisico   numeric(9,8) NOT NULL CHECK (perc_fisico >= 0),
    PRIMARY KEY (eap_item_id, mes)
);

-- ---------------------------------------------------------------------
-- 6. PLANO DE CONTAS E REALIZADO (Sienge)
-- ---------------------------------------------------------------------
-- as ~180 linhas de detalhe da aba "Viabilidades Mensais" viram ISTO:
CREATE TABLE conta (
    id            bigserial PRIMARY KEY,
    codigo        text UNIQUE NOT NULL,      -- '07.000.000.003', '1 - OBRA'
    descricao     text NOT NULL,             -- 'Projeto Estrutural'
    grupo         text NOT NULL CHECK (grupo IN
                  ('RECEITA','DEDUCAO_VGV','DEDUCAO_RECEITA','GASTO','FINANCEIRO','APORTE')),
    linha_dre     text NOT NULL,             -- '(-) Incorporação - Outros'  ← agregador
    sinal         smallint NOT NULL DEFAULT -1 CHECK (sinal IN (-1, 1))
);
CREATE INDEX ON conta (linha_dre);
COMMENT ON TABLE conta IS
 'linha_dre é o que a planilha faz com SUM(E42:E143): o agrupamento de dezenas de
  contas analíticas do Sienge nas ~19 linhas da DRE de viabilidade.';

-- lançamentos de caixa vindos do Sienge (aba Fin_Obra)
CREATE TABLE movimento_realizado (
    id                    bigserial PRIMARY KEY,
    empreendimento_id     bigint NOT NULL REFERENCES empreendimento,
    sienge_bank_movement_id bigint UNIQUE,        -- Fin_Obra.bankMovementId
    conta_id              bigint REFERENCES conta,
    data_movimento        date NOT NULL,          -- bankMovementDate
    mes_competencia       date NOT NULL,          -- Fin_Obra "Fim Mês" (fim do mês do movimento)
    valor                 numeric(16,2) NOT NULL, -- Fin_Obra "Valor" já rateado
    rateio_categoria      numeric(7,4),           -- financialCategoryRate
    rateio_departamento   numeric(7,4),           -- coluna "%"
    unidade_id            bigint REFERENCES unidade,
    fornecedor            text,
    centro_custo          text,
    conciliado            boolean NOT NULL DEFAULT false
);
CREATE INDEX ON movimento_realizado (empreendimento_id, mes_competencia, conta_id);

CREATE OR REPLACE FUNCTION preenche_competencia() RETURNS trigger AS $$
BEGIN
    NEW.mes_competencia := fim_do_mes(NEW.data_movimento);
    RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER trg_competencia BEFORE INSERT OR UPDATE ON movimento_realizado
    FOR EACH ROW EXECUTE FUNCTION preenche_competencia();
COMMENT ON COLUMN movimento_realizado.valor IS
 'Fin_Obra!BC = bankMovementAmount * financialCategoryRate * % / 100 — o rateio JÁ
  aplicado. Guardar rateado evita o erro de somar o bruto duas vezes.';

-- parcelas a receber e recebidas (abas Receber / Recebido)
CREATE TABLE parcela_receber (
    id                bigserial PRIMARY KEY,
    contrato_id       bigint REFERENCES contrato ON DELETE CASCADE,
    unidade_id        bigint NOT NULL REFERENCES unidade,
    sienge_bill_id    integer,
    sienge_installment_id integer,
    numero_parcela    text,
    condicao          smallint NOT NULL,          -- Receber "Condição": 1=poupança 2/3=pós-chaves
    vencimento        date NOT NULL,
    mes_vencimento    date NOT NULL,
    valor             numeric(16,2) NOT NULL,     -- Receber "Valor" (saldo corrigido)
    indexador         text,
    UNIQUE (sienge_bill_id, sienge_installment_id)
);
CREATE OR REPLACE FUNCTION preenche_vencimento() RETURNS trigger AS $$
BEGIN
    NEW.mes_vencimento := fim_do_mes(NEW.vencimento);
    RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER trg_vencimento BEFORE INSERT OR UPDATE ON parcela_receber
    FOR EACH ROW EXECUTE FUNCTION preenche_vencimento();

CREATE INDEX ON parcela_receber (unidade_id, mes_vencimento);
CREATE INDEX ON parcela_receber (condicao, mes_vencimento);

CREATE TABLE parcela_recebida (
    id                bigserial PRIMARY KEY,
    unidade_id        bigint NOT NULL REFERENCES unidade,
    sienge_bill_id    integer,
    sienge_installment_id integer,
    data_recebimento  date NOT NULL,
    valor_liquido     numeric(16,2) NOT NULL,     -- Recebido "Valor"
    empresa_id        integer                     -- 26 = SPE, 90 = permuta/terceiros
);
CREATE INDEX ON parcela_recebida (unidade_id, data_recebimento);

-- ---------------------------------------------------------------------
-- 7. RESULTADO DA PROJEÇÃO (materializado por rodada do motor)
-- ---------------------------------------------------------------------
CREATE TABLE rodada (
    id            bigserial PRIMARY KEY,
    cenario_id    bigint NOT NULL REFERENCES cenario ON DELETE CASCADE,
    executada_em  timestamptz NOT NULL DEFAULT now(),
    executada_por text,
    hash_entradas text,     -- impede recalcular o que não mudou / dá auditoria
    UNIQUE (cenario_id, hash_entradas)
);

CREATE TABLE fluxo_projetado (
    rodada_id     bigint NOT NULL REFERENCES rodada ON DELETE CASCADE,
    linha_dre     text   NOT NULL,
    mes           date   NOT NULL,
    valor         numeric(16,2) NOT NULL,
    PRIMARY KEY (rodada_id, linha_dre, mes)
);
CREATE INDEX ON fluxo_projetado (rodada_id, mes);

CREATE TABLE indicador (
    rodada_id           bigint PRIMARY KEY REFERENCES rodada ON DELETE CASCADE,
    vgv                 numeric(16,2),
    receita_liquida     numeric(16,2),
    lucro               numeric(16,2),
    margem              numeric(9,6),
    custo_m2_privativa  numeric(14,4),
    preco_m2_vgv        numeric(14,4),
    eficiencia          numeric(9,6),
    exposicao_maxima    numeric(16,2),
    mes_exposicao       date,
    vpl                 numeric(16,2),
    tir_anual           numeric(9,6),    -- pode ser NULL: fluxo com múltiplas raízes
    mtir_anual          numeric(9,6),
    aporte_necessario   numeric(16,2)
);

-- ---------------------------------------------------------------------
-- 8. A VISÃO QUE A ABA "VIABILIDADE" ENTREGA
--    orçado (congelado) × atualizado (rodada mais nova) × realizado (Sienge)
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW v_viabilidade AS
WITH ultima_rodada AS (
    SELECT DISTINCT ON (c.empreendimento_id, c.tipo)
           r.id AS rodada_id, c.empreendimento_id, c.tipo, c.nome AS cenario
      FROM rodada r
      JOIN cenario c ON c.id = r.cenario_id
     ORDER BY c.empreendimento_id, c.tipo, r.executada_em DESC
),
proj AS (
    SELECT u.empreendimento_id, u.tipo, f.linha_dre, sum(f.valor) AS valor
      FROM fluxo_projetado f
      JOIN ultima_rodada u ON u.rodada_id = f.rodada_id
     GROUP BY 1, 2, 3
),
real AS (
    SELECT m.empreendimento_id, c.linha_dre, sum(m.valor) AS valor
      FROM movimento_realizado m
      JOIN conta c ON c.id = m.conta_id
     GROUP BY 1, 2
)
SELECT COALESCE(o.empreendimento_id, a.empreendimento_id, r.empreendimento_id)
                                              AS empreendimento_id,
       COALESCE(o.linha_dre, a.linha_dre, r.linha_dre) AS linha_dre,
       COALESCE(o.valor, 0)                   AS orcado,
       COALESCE(a.valor, 0)                   AS atualizado,
       COALESCE(r.valor, 0)                   AS realizado,
       COALESCE(a.valor, 0) - COALESCE(r.valor, 0) AS a_realizar,
       CASE WHEN COALESCE(o.valor,0) <> 0
            THEN COALESCE(a.valor,0) / o.valor - 1 END AS variacao
  FROM (SELECT * FROM proj WHERE tipo = 'orcado')    o
  FULL JOIN (SELECT * FROM proj WHERE tipo='projecao') a
       ON a.empreendimento_id = o.empreendimento_id AND a.linha_dre = o.linha_dre
  FULL JOIN real r
       ON r.empreendimento_id = COALESCE(o.empreendimento_id, a.empreendimento_id)
      AND r.linha_dre = COALESCE(o.linha_dre, a.linha_dre);

-- ---------------------------------------------------------------------
-- 9. TRAVAS QUE A PLANILHA NÃO TEM
-- ---------------------------------------------------------------------
-- a curva física de cada atividade tem de somar 100%
CREATE OR REPLACE FUNCTION checa_curva_cronograma() RETURNS trigger AS $$
DECLARE s numeric;
BEGIN
    SELECT sum(perc_fisico) INTO s FROM cronograma_item
     WHERE eap_item_id = COALESCE(NEW.eap_item_id, OLD.eap_item_id);
    IF s IS NOT NULL AND abs(s - 1) > 0.0001 THEN
        RAISE EXCEPTION 'curva física da atividade % soma %, deveria somar 1',
              COALESCE(NEW.eap_item_id, OLD.eap_item_id), s;
    END IF;
    RETURN NULL;
END $$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER trg_curva_cronograma
    AFTER INSERT OR UPDATE OR DELETE ON cronograma_item
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION checa_curva_cronograma();

-- os pesos da EAP têm de somar 100%
CREATE OR REPLACE FUNCTION checa_pesos_eap() RETURNS trigger AS $$
DECLARE s numeric;
BEGIN
    SELECT sum(peso) INTO s FROM eap_item
     WHERE orcamento_id = COALESCE(NEW.orcamento_id, OLD.orcamento_id);
    IF s IS NOT NULL AND abs(s - 1) > 0.0001 THEN
        RAISE EXCEPTION 'pesos da EAP somam %, deveriam somar 1', s;
    END IF;
    RETURN NULL;
END $$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER trg_pesos_eap
    AFTER INSERT OR UPDATE OR DELETE ON eap_item
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION checa_pesos_eap();

-- =====================================================================
-- 002 — controle de importação (o que a planilha não tem: rastro)
-- =====================================================================
CREATE TABLE importacao (
    id                bigserial PRIMARY KEY,
    empreendimento_id bigint NOT NULL REFERENCES empreendimento ON DELETE CASCADE,
    fonte             text NOT NULL CHECK (fonte IN
                      ('unidades','contratos','receber','recebido','fin_obra','planilha_completa')),
    origem            text NOT NULL CHECK (origem IN ('upload','api_sienge')),
    arquivo           text,
    linhas_lidas      integer NOT NULL DEFAULT 0,
    linhas_gravadas   integer NOT NULL DEFAULT 0,
    avisos            jsonb   NOT NULL DEFAULT '[]'::jsonb,
    importado_em      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON importacao (empreendimento_id, fonte, importado_em DESC);

-- data de corte: até quando o realizado é considerado fechado
ALTER TABLE empreendimento ADD COLUMN mes_corte_realizado date;
COMMENT ON COLUMN empreendimento.mes_corte_realizado IS
 'Substitui a fronteira posicional J:AC / AF:DQ da planilha. Movimentos até
  esta data contam como REALIZADO; a projeção começa no mês seguinte.';
