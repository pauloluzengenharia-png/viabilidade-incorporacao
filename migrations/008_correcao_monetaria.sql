-- =====================================================================
-- 008 — correção monetária da carteira
--
-- A planilha projeta tudo em valores nominais: uma parcela de 2031 entra no
-- fluxo com o mesmo poder de compra de hoje, apesar de os contratos terem
-- indexador cadastrado no Sienge (Contratos.correctionType). Numa carteira de
-- 60 parcelas isso subestima a receita de forma relevante.
--
-- Aqui o índice é premissa do cenário, com série histórica quando existe e
-- taxa projetada para o futuro. Índice diferente antes e depois das chaves,
-- que é como o contrato costuma ser escrito: INCC durante a obra, IGP-M ou
-- IPCA no período de repasse.
-- =====================================================================
SET search_path = viab, public;

CREATE TABLE indice_economico (
    codigo    text PRIMARY KEY,          -- 'INCC-DI', 'IGP-M', 'IPCA'
    nome      text NOT NULL,
    fonte     text
);

CREATE TABLE indice_mensal (
    indice_codigo text NOT NULL REFERENCES indice_economico ON DELETE CASCADE,
    mes           date NOT NULL,          -- sempre o último dia do mês
    variacao      numeric(9,6) NOT NULL,  -- fração: 0.0061 = 0,61% no mês
    PRIMARY KEY (indice_codigo, mes)
);
COMMENT ON COLUMN indice_mensal.variacao IS
 'Variação do mês em fração, não em pontos percentuais. 0,61% grava 0.006100.';

-- qual índice o cenário usa em cada fase
ALTER TABLE cenario
    ADD COLUMN indice_ate_chaves  text REFERENCES indice_economico,
    ADD COLUMN indice_apos_chaves text REFERENCES indice_economico;

COMMENT ON COLUMN cenario.indice_ate_chaves IS
 'Índice aplicado às parcelas até a entrega. NULL = projeção nominal, que é
  o que a planilha fazia. A taxa usada para meses sem série histórica vem da
  premissa numérica `indice_projetado_aa`.';

INSERT INTO indice_economico (codigo, nome, fonte) VALUES
  ('INCC-DI', 'Índice Nacional de Custo da Construção — Disponibilidade Interna', 'FGV'),
  ('IGP-M',   'Índice Geral de Preços — Mercado', 'FGV'),
  ('IPCA',    'Índice Nacional de Preços ao Consumidor Amplo', 'IBGE')
ON CONFLICT (codigo) DO NOTHING;

-- INCC-DI de 2026, publicado pela FGV (jan a jul).
-- Acumulado em 12 meses até jul/2026: 6,4594%.
INSERT INTO indice_mensal (indice_codigo, mes, variacao) VALUES
  ('INCC-DI', DATE '2026-01-31', 0.0072),
  ('INCC-DI', DATE '2026-02-28', 0.0028),
  ('INCC-DI', DATE '2026-03-31', 0.0054),
  ('INCC-DI', DATE '2026-04-30', 0.0100),
  ('INCC-DI', DATE '2026-05-31', 0.0088),
  ('INCC-DI', DATE '2026-06-30', 0.0078),
  ('INCC-DI', DATE '2026-07-31', 0.0061)
ON CONFLICT (indice_codigo, mes) DO NOTHING;
