-- =====================================================================
-- 003 — qual cenário responde pela "viabilidade atualizada"
--
-- A planilha resolve isso por posição: a coluna Q da aba VIABILIDADE puxa
-- do bloco realista de SIMULAÇÕES, e ponto. Aqui vira uma flag explícita,
-- para que trocar o cenário de referência seja uma decisão registrada e não
-- uma alteração de fórmula.
-- =====================================================================
SET search_path = viab, public;

ALTER TABLE cenario ADD COLUMN principal boolean NOT NULL DEFAULT false;

-- no máximo um cenário principal por empreendimento
CREATE UNIQUE INDEX cenario_principal_unico
    ON cenario (empreendimento_id) WHERE principal;

COMMENT ON COLUMN cenario.principal IS
 'O cenário de projeção que alimenta a coluna "atualizada" da viabilidade.
  Tipicamente o realista. O orçado (tipo = ''orcado'') nunca é principal.';
