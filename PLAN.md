# Plano do Projeto: Content Pipeline

Pipeline automatizada de geração de vídeo curto (YouTube/TikTok/Reels/Twitter) a partir de conteúdo bruto ingerido de fontes públicas. Estilo n8n, mas 100% programático em Python.

---

## 1. Visão Geral

**Entrada:** URL de post (Reddit, Medium, HackerNews, YouTube, etc.)
**Saída:** vídeo `.mp4` renderizado (16:9 e/ou 9:16), pronto para publicação em plataformas de vídeo.

**Fluxo:**

```
[Ingest] → [Storage] → [LLM Script] → [TTS] → [Media Gen] → [FFmpeg Render] → [Publish?]
```

Cada etapa isolada, retry independente, saída persistida em storage compartilhado antes de encadear a próxima.

---

## 2. Arquitetura Macro

| Etapa | Componente | Responsabilidade | Output |
|-------|-----------|------------------|--------|
| 1. Ingestão | FastAPI trigger + módulos `app/ingest/*` | Capturar conteúdo bruto de fontes públicas e normalizar em contrato único | `IngestPayload` JSON |
| 2. Orquestração | Celery + Redis (broker + result backend) | Encadear tarefas, retries, timeouts, persistência de estado | Task chain assíncrono |
| 3. Inteligência | LLM (OpenAI/Anthropic) | Filtro de ruído, geração de roteiro estruturado + prompts visuais | `VideoScript` JSON |
| 4. Síntese | ElevenLabs (TTS) + Leonardo/Stability (imagem) | Áudio + word-level timestamps + imagens complementares | `.mp3`, `timestamps.json`, `.jpg`s |
| 5. Renderização | FFmpeg via subprocess Python | Montagem, Ken Burns, legendas karaokê, multi-aspect | `.mp4` 16:9 e 9:16 |

---

## 3. Stack Escolhida

| Categoria | Escolha | Alternativas descartadas |
|-----------|---------|--------------------------|
| Linguagem | Python 3.13 | 3.14 (quebra wheel pydantic), 3.12 |
| Package manager | pip + `requirements.txt` | uv, poetry (over-engineering pro MVP) |
| API HTTP | FastAPI | Flask (menos ergonomia), gRPC (overkill) |
| Fila | Celery + Redis | RabbitMQ (mais complexo), SQS (managed cost) |
| DB metadata | Postgres local na VPS | DynamoDB (sem uso analítico), SQLite (multi-worker) |
| Blob storage | Cloudflare R2 (S3-API) | AWS S3 (egress fee mata upload multi-plataforma), GCS |
| Deploy | VPS única, Docker Compose | K8s, ECS, Fly.io (overkill MVP) |
| Web scraping | `trafilatura` + `beautifulsoup4` | Playwright (pesado, adiar), BS4 puro (parser frágil) |
| Reddit API | PRAW | Endpoint público `.json` (bloqueado por Reddit desde 2023) |
| Validação | Pydantic v2 | dataclasses puros (sem validação), attrs |
| Testes | pytest + pytest-mock | unittest puro |
| Env config | python-dotenv | pydantic-settings (adiar) |

---

## 4. Contrato Central: `IngestPayload`

Todo scraper normaliza pra este schema antes de despachar downstream:

```python
class IngestPayload(BaseModel):
    source: Literal["reddit", "twitter", "medium", "youtube", "instagram"]
    original_url: HttpUrl
    raw_title: str
    raw_content: str
    media_urls: list[HttpUrl] = []
    target_platforms: list[TargetPlatform] = []
    metadata: dict = {}
```

`metadata` é livre por scraper (score/comments no Reddit, author/date no Medium, etc.). Downstream não deve depender de campos específicos.

---

## 5. Storage Layer

### Blob (Cloudflare R2 em prod, MinIO em dev — mesma API S3)

Layout por job:

```
jobs/{job_id}/
  input.json                  # IngestPayload normalizado
  script.json                 # output do LLM
  audio/
    narration.mp3
    timestamps.json           # word-level ElevenLabs
  images/
    000_scraped.jpg
    001_generated.jpg
  subtitles/
    pt.ass
  output/
    final_16x9.mp4
    final_9x16.mp4
  meta.json                   # status, custo, timing por etapa
```

**Contrato entre tasks:** só passa `job_id`. Nunca bytes. Cada task lê do storage o que precisa, escreve o que produz. Retry idempotente por checagem de existência.

### Cache de idempotência

Chave = hash canonicalizado do input. Antes de chamar API paga:
```
cache/tts/{hash}.mp3
cache/llm/{hash}.json
cache/image/{hash}.jpg
```
Se existe → copia pra job path. Senão → chama API, salva em cache + job.

### Metadata (Postgres local na VPS)

```sql
CREATE TABLE jobs (
  job_id UUID PRIMARY KEY,
  source TEXT NOT NULL,
  original_url TEXT,
  status TEXT NOT NULL,          -- pending|running|done|failed
  current_step TEXT,             -- ingest|llm|tts|render
  target_platforms TEXT[],
  cost_cents INT DEFAULT 0,
  error_message TEXT,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now(),
  output_urls JSONB
);

CREATE INDEX idx_jobs_status ON jobs(status, created_at DESC);

CREATE TABLE job_steps (
  id BIGSERIAL PRIMARY KEY,
  job_id UUID REFERENCES jobs(job_id) ON DELETE CASCADE,
  step TEXT NOT NULL,
  status TEXT NOT NULL,
  attempt INT DEFAULT 1,
  cost_cents INT DEFAULT 0,
  duration_ms INT,
  error TEXT,
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ
);
```

### Lifecycle policies (R2)

- `jobs/*/audio/`, `jobs/*/images/`: expira em 30d (regeneráveis)
- `jobs/*/output/`: infrequent access após 30d
- `cache/*`: expira em 90d sem acesso
- `jobs/*/{script,timestamps,meta}.json`: sem expiração

---

## 6. FFmpeg — Opção A: temp → render → upload

Escolha do MVP:
- Worker baixa arquivos necessários do R2 pra `/tmp/{job_id}/`
- FFmpeg renderiza local (subprocess Python)
- Upload do `.mp4` final pro R2
- `finally:` limpa temp dir

Descartado: FUSE mount S3 (latência, complexidade).

---

## 7. Deploy — VPS única

**Setup:**
- 1 VPS (Hetzner CX22 ou similar — 2 vCPU, 4GB RAM, 40GB SSD, ~€4/mês)
- Docker Compose com tudo junto:
  - `api` (FastAPI)
  - `worker` (Celery)
  - `redis`
  - `postgres`
- FFmpeg binário no host (via `apt install ffmpeg` no `Dockerfile`)
- R2 externo (blobs)

**Deploy:** `git pull && docker compose up -d --build`

**Sem** (por ora):
- Celery beat / cron scheduler — trigger 100% manual
- CDN
- HTTPS / domínio (só IP:port no MVP)
- Multi-worker / scaling horizontal
- K8s, ECS
- Prometheus stack

---

## 8. Módulos: Status Atual

### ✅ Módulo 1: Ingestão Reddit (`app/ingest/reddit.py`)

- PRAW oficial (endpoint público `.json` bloqueado desde 2023)
- Injeção de client pra testes: `fetch_reddit_post(url, client=None)`
- Fixture loader: aceita Reddit Listing array real ou dict flat sintético
- Extração de mídia: `url_overridden_by_dest`, `preview.images`, `media_metadata` (galeria)
- URL derivada de `permalink` quando ausente
- 14 testes
- **Bloqueio atual:** conta Reddit criada, mas app não registrado (erro 500 em `/prefs/apps`). Fixture cobre dev enquanto isso.

### ✅ Módulo 1: Ingestão Web/Medium (`app/ingest/web.py`)

- `trafilatura` extrai title, body, autor, data
- `beautifulsoup4` complementa com `<img>` do `<article>`
- Detecção de source por hostname (só `medium.com` por ora)
- Injeção via `html=` param pra testes
- Fixture HTML stripped (44 `<script>/<iframe>` removidos por policy git-dlp Nubank)
- 13 testes
- **Trade-offs:** Cloudflare pode 403 em VPS. Paywall Medium retorna preview. Imagens inline via JS não são capturadas (só hero/avatar).

### CLI atual

```bash
python -m app reddit --fixture tests/fixtures/reddit_sample.json
python -m app reddit --url "https://reddit.com/..."       # precisa .env com PRAW
python -m app web    --fixture tests/fixtures/medium_sample.html --as-url "..."
python -m app web    --url "https://medium.com/..."       # rede ao vivo
```

---

## 9. Módulos: Pendentes

### Módulo 2: Orquestração
- FastAPI `POST /jobs` recebe URL, cria row em `jobs`, despacha Celery
- FastAPI `GET /jobs/{id}` retorna status + presigned URLs
- Celery task chain: `ingest → llm → tts → render`
- Retry exponential backoff, isolamento total de falhas
- Redis: broker + result backend

### Módulo 3: LLM (roteiro)
- Recebe `IngestPayload`
- Retorna `VideoScript` Pydantic com `hook`, `narration_lines`, `visual_prompts`, `target_duration_seconds`
- Few-shot prompting via templates de sucesso (Reddit posts, Medium articles)
- **Structured output obrigatório** — não parsing de string. Retry com feedback do erro de schema.
- Stub determinístico pra dev sem API key

### Módulo 4a: TTS
- ElevenLabs API
- Extração obrigatória de **word-level timestamps** (nativo da API) → base pra legendas karaokê
- Cache por hash do texto pra evitar re-síntese
- Stub retorna .mp3 silencioso + timestamps fake pra dev sem key

### Módulo 4b: Media (imagens complementares)
- Prioridade: `media_urls` do `IngestPayload` (imagens do post original)
- Fallback: geração via Leonardo.ai ou Stability
- Um prompt por `visual_prompts[i]` do roteiro
- Cache por hash do prompt

### Módulo 5: Render FFmpeg
- Scripts Python constroem subprocess FFmpeg parametrizado
- Timestamps de áudio mapeiam tempo de exibição de cada imagem
- Ken Burns via `zoompan` filter (movimento sutil)
- Legendas queimadas via filtro `subtitles` (ASS format pra karaokê)
- Multi-proporção: 16:9 e 9:16 no mesmo pipeline
- Legendas reposicionadas verticalmente pra 9:16 (evitar zonas cegas TikTok/Reels)

### Módulo 6: Publish (out of scope MVP)
- YouTube Data API (upload OK)
- TikTok API (fechada, provavelmente manual)
- Instagram Graph API (upload OK, requer conta business)
- Twitter/X API (paga desde 2023)

---

## 10. Trade-offs Declarados

1. **Reddit sem PRAW oficial = impossível.** Endpoint público `.json` retorna 403 em datacenter/VPS.
2. **Medium sem headless browser = imagens incompletas.** JS lazy-load não roda. Playwright adiado.
3. **Sem Celery beat = zero automação de scraping.** Trigger 100% manual via `POST /jobs`.
4. **Sem HTTPS/CDN no MVP.** API só via IP:port. Uploads vão direto server-side pras plataformas.
5. **Cloudflare pode bloquear IP VPS.** Aceito. Mitigação futura: proxy rotation ou headless browser.
6. **Sem multi-worker.** 1 processo Celery, N threads. Escala vertical primeiro.
7. **Cache de idempotência por hash do input canonicalizado.** Se input muda 1 char, chama API de novo. OK.

---

## 11. Observabilidade Mínima (pra depois)

- **Trace ID** = `job_id`. Propagado entre tasks. Log estruturado em JSON.
- **Métricas por step:** latência, taxa de retry, custo em cents.
- **Dead-letter queue** pra tasks que estouram retries.
- **Dashboard interno:** Metabase/Grafana sobre Postgres. Depois.

---

## 12. Segurança

- `.env` no `.gitignore`. Sempre. `.env.example` commitado como template.
- Presigned URLs pra download de output (nunca bucket público).
- R2 com SSE (server-side encryption).
- Sem senha de Reddit no `.env` (só client_id/secret do app OAuth).
- Git-DLP hook Nubank ativo — bloqueou reCAPTCHA key em fixture Medium. Fix: strip de `<script>` tags. Hook **não** bypassado.

---

## 13. Roadmap Sugerido

### Fase 1 — MVP mínimo end-to-end (em andamento)
- [x] Módulo 1.1: Reddit ingest (com fixture, PRAW pendente de app registrado)
- [x] Módulo 1.2: Web/Medium ingest
- [ ] Storage local (`app/storage.py`) — wrapper S3 (funciona com R2 e MinIO)
- [ ] LLM stub → schema Pydantic determinístico (sem API key)
- [ ] TTS stub → .mp3 silencioso + timestamps fake
- [ ] FFmpeg mínimo — 1 imagem estática + áudio → `.mp4` 16:9
- [ ] Postgres + tabelas `jobs`/`job_steps`
- [ ] Celery + Redis + FastAPI `POST /jobs`
- [ ] Docker Compose end-to-end local

### Fase 2 — API keys reais
- [ ] Reddit app registrado → PRAW real
- [ ] LLM real (OpenAI ou Anthropic) com structured output
- [ ] ElevenLabs real + word-level timestamps
- [ ] Legendas karaokê ASS a partir dos timestamps

### Fase 3 — Polimento visual
- [ ] Ken Burns via zoompan
- [ ] Imagens geradas (Leonardo/Stability) como fallback
- [ ] Multi-proporção 16:9 e 9:16 com crop matricial
- [ ] Reposicionamento vertical de legendas em 9:16

### Fase 4 — Publicação
- [ ] YouTube upload
- [ ] Fluxo de aprovação humana (staging antes de publicar)
- [ ] Cost tracker por job
- [ ] Rate limiting por origem

---

## 14. Pendências / Decisões em Aberto

- [ ] **VPS provider final:** Hetzner CX22 (recomendação atual). Contabo mais barato, DO mais fácil.
- [ ] **LLM inicial:** OpenAI ou Anthropic. Escolher 1 pra MVP.
- [ ] **Voz clonada ElevenLabs:** qual? Licença/consentimento?
- [ ] **Volume esperado:** vídeos/dia? Afeta decisões de infra.
- [ ] **Humano aprova antes de publicar?** Ou 100% autônomo?
- [ ] **Segundo scraper:** HackerNews API (sem key), YouTube transcript, ou Playwright para Medium?

---

## 15. Estrutura Atual do Repo

```
content-pipeline/
├── .env.example                       # template creds Reddit
├── .gitignore                         # venv, .env, pycache
├── PLAN.md                            # este doc
├── requirements.txt                   # praw, pydantic, trafilatura, bs4, requests, dotenv
├── requirements-dev.txt               # + pytest, pytest-mock
├── app/
│   ├── __init__.py
│   ├── __main__.py                    # CLI subcomandos reddit/web
│   ├── config.py                      # load_reddit_config() via env
│   ├── schemas.py                     # IngestPayload
│   └── ingest/
│       ├── __init__.py
│       ├── reddit.py                  # PRAW + fixture loader
│       └── web.py                     # trafilatura + BS4
└── tests/
    ├── __init__.py
    ├── test_reddit.py                 # 14 testes
    ├── test_web.py                    # 13 testes
    └── fixtures/
        ├── reddit_sample.json         # Listing shape real
        └── medium_sample.html         # HTML stripped
```

**Total: 27 testes, todos verdes.**
