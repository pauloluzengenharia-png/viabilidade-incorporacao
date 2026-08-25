-- =====================================================================
-- 002 — preço de tabela versionado por unidade e parcelas do terreno
--
-- A planilha guarda o preço de tabela na própria linha da unidade
-- (Comercial!D), o que apaga o histórico a cada reajuste. Aqui o preço é
-- um registro datado: dá para responder "por quanto essa sala estava em
-- março?" e para reconstruir o VGV de qualquer data.
-- =====================================================================
SET search_path = viab, public;

CREATE TABLE preco_unidade (
    id            bigserial PRIMARY KEY,
    unidade_id    bigint NOT NULL REFERENCES unidade ON DELETE CASCADE,
    preco_bruto   numeric(16,2) NOT NULL CHECK (preco_bruto >= 0),
    vigente_desde date NOT NULL DEFAULT CURRENT_DATE,
    observacao    text,
    UNIQUE (unidade_id, vigente_desde)
);
CREATE INDEX ON preco_unidade (unidade_id, vigente_desde DESC);

-- SIMULAÇÕES M34: o pagamento do terreno é uma lista de parcelas, não um total
CREATE TABLE premissa_terreno (
    id          bigserial PRIMARY KEY,
    cenario_id  bigint NOT NULL REFERENCES cenario ON DELETE CASCADE,
    ordem       smallint NOT NULL,
    valor       numeric(16,2) NOT NULL CHECK (valor >= 0),
    vencimento  date,
    UNIQUE (cenario_id, ordem)
);
