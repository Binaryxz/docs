-- UF do processo, usada pelo matching (reduz o universo de empresas
-- candidatas no fuzzy match) e pelos filtros da tela de Leads.
-- Derivada do alias do tribunal (ex: api_publica_tjsp -> SP) no momento da
-- coleta — ver app/tribunais.py.
ALTER TABLE processos ADD COLUMN IF NOT EXISTS uf VARCHAR(2);
CREATE INDEX IF NOT EXISTS idx_processos_uf ON processos (uf);
