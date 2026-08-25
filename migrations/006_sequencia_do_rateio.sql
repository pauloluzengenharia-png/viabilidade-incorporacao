-- =====================================================================
-- 006 — o mesmo lançamento pode se repartir duas vezes na MESMA conta
--
-- O lançamento 591000 da Kiev aparece duas vezes em "Projeto Hidrossanitário"
-- e duas em "Projeto Elétrico". Nem o id do movimento nem o par
-- (movimento, conta) identificam a linha: falta uma ordem dentro do rateio,
-- que o export do Sienge não traz. O importador numera na ordem de leitura.
-- =====================================================================
SET search_path = viab, public;

DROP INDEX IF EXISTS movimento_rateio_unico;

ALTER TABLE movimento_realizado
    ADD COLUMN sequencia smallint NOT NULL DEFAULT 1;

CREATE UNIQUE INDEX movimento_rateio_unico
    ON movimento_realizado (sienge_bank_movement_id, conta_id, sequencia)
    WHERE sienge_bank_movement_id IS NOT NULL;

COMMENT ON COLUMN movimento_realizado.sequencia IS
 'Ordem da linha dentro do rateio de um mesmo lançamento bancário na mesma
  conta. Sem ela, o segundo rateio sobrescreve o primeiro na reimportação.';
