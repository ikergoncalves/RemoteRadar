# RemoteRadar

Pipeline de ETL em Python que coleta vagas remotas de tecnologia de APIs públicas,
limpa e transforma os dados, carrega num data warehouse PostgreSQL e — nas próximas
fases — gera um dashboard de tendências: linguagens mais pedidas, faixas salariais,
empresas que mais contratam remoto e evolução ao longo do tempo.

> **Projeto em fases.** Esta é a Fase 1 de 7: estrutura do repositório e extração
> da primeira fonte (Remotive), salvando o payload bruto no schema `raw` do
> PostgreSQL. Novas fontes (RemoteOK, Adzuna), transformações e o dashboard
> chegam nas fases seguintes.

## Stack planejada

| Camada              | Ferramenta                                      |
| ------------------- | ----------------------------------------------- |
| Extração            | Python + [httpx](https://www.python-httpx.org/) |
| Orquestração        | Prefect (open-source)                           |
| Transformação       | dbt-core                                        |
| Warehouse           | PostgreSQL (Supabase ou Railway free tier)      |
| Qualidade de dados  | Great Expectations                              |
| Dashboard           | Streamlit (Streamlit Community Cloud)           |
| Testes              | Pytest                                          |
| CI / Agendamento    | GitHub Actions (cron diário)                    |

## Estrutura atual

```
remoteradar/
├── src/remoteradar/
│   ├── config.py            # Leitura de variáveis de ambiente (.env)
│   ├── load.py              # Carga de payloads brutos no PostgreSQL
│   └── extract/
│       └── remotive.py      # Extração da API da Remotive
├── sql/
│   └── 001_create_raw_remotive_jobs.sql   # DDL da tabela de landing
├── tests/                   # Testes com HTTP mockado (não bate na API real)
├── .env.example             # Variáveis de ambiente documentadas
└── pyproject.toml           # Dependências e configuração de ferramentas
```

## Como rodar localmente

Requer Python 3.11+.

```bash
# 1. Criar e ativar o ambiente virtual
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/macOS

# 2. Instalar o projeto com dependências de desenvolvimento
pip install -e ".[dev]"

# 3. Configurar variáveis de ambiente
# Copie .env.example para .env e preencha DATABASE_URL
```

### Variáveis de ambiente

| Variável           | Obrigatória | Descrição                                                        |
| ------------------ | ----------- | ---------------------------------------------------------------- |
| `DATABASE_URL`     | Sim (carga) | String de conexão do PostgreSQL (`postgresql://user:pass@host:5432/db`) |
| `REMOTIVE_API_URL` | Não         | URL base da API da Remotive (padrão: endpoint público)           |

### Criar a tabela raw no PostgreSQL

```bash
psql "$DATABASE_URL" -f sql/001_create_raw_remotive_jobs.sql
```

### Executar a extração

```bash
python -m remoteradar.extract.remotive
```

Busca as vagas de tech na Remotive e insere o payload bruto (JSONB) em
`raw.remotive_jobs`, com timestamp de coleta. Sem `DATABASE_URL` configurada,
o script falha com uma mensagem de erro explicando como corrigir.

### Testes e lint

```bash
pytest
ruff check .
```

## Licença

[MIT](LICENSE)
