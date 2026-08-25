-- =====================================================================
-- 005 — um lançamento bancário pode virar mais de um movimento
--
-- Descoberto na migração: `bankMovementId` NÃO é chave única. Quando o
-- pagamento é rateado entre duas categorias financeiras (na Kiev são 8
-- lançamentos com rateio 50/50), o Sienge devolve a mesma movimentação
-- bancária em duas linhas, cada uma com sua categoria e seu percentual.
--
-- Com a unicidade no id do movimento, a segunda linha sobrescrevia a
-- primeira e metade do valor desaparecia do incorrido — silenciosamente.
-- A chave natural é (movimento bancário, conta).
-- =====================================================================
SET search_path = viab, public;

ALTER TABLE movimento_realizado
    DROP CONSTRAINT IF EXISTS movimento_realizado_sienge_bank_movement_id_key;

CREATE UNIQUE INDEX movimento_rateio_unico
    ON movimento_realizado (sienge_bank_movement_id, conta_id)
    WHERE sienge_bank_movement_id IS NOT NULL;
