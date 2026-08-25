-- =====================================================================
-- 004 — quais unidades entram na viabilidade
--
-- Descoberto na migração da Kiev: o cadastro do Sienge e a lista que a
-- planilha usa para calcular o VGV NÃO são a mesma lista.
--
--   · SALA 1 - G 02 e SALA 2 - G 02 existem no Sienge e ficaram de fora
--     da aba Comercial (68,26 m² que não entram no VGV);
--   · ROOFTOP entra no VGV como permuta (R$ 8.122.880) e não existe no
--     cadastro de unidades do Sienge.
--
-- Na planilha essa divergência é invisível: some na diferença entre duas
-- abas. Aqui vira uma flag por unidade, com o motivo registrado — dá para
-- auditar e dá para corrigir sem mexer em fórmula nenhuma.
-- =====================================================================
SET search_path = viab, public;

ALTER TABLE unidade
    ADD COLUMN considerar_na_viabilidade boolean NOT NULL DEFAULT true,
    ADD COLUMN motivo_exclusao text,
    ADD COLUMN origem_cadastro text NOT NULL DEFAULT 'sienge'
        CHECK (origem_cadastro IN ('sienge', 'planilha', 'manual'));

CREATE INDEX ON unidade (empreendimento_id, considerar_na_viabilidade);

COMMENT ON COLUMN unidade.considerar_na_viabilidade IS
 'Falso para unidades que existem no empreendimento mas ficam fora do VGV
  do estudo. Exige motivo — exclusão silenciosa é o que a planilha fazia.';

ALTER TABLE unidade ADD CONSTRAINT exclusao_precisa_de_motivo
    CHECK (considerar_na_viabilidade OR motivo_exclusao IS NOT NULL);
