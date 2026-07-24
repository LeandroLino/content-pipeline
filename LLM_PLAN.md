# LLM Module — Decisões e Planejamento

Documento de referência para o **Módulo 3: LLM (roteiro)** do `PLAN.md` (seção 9).
Serve pra guardar decisões, contexto e pendências entre sessões, sem precisar
re-explicar tudo do zero.

---

## 1. Status Atual da Pipeline (contexto de entrada)

Módulos de ingestão já funcionando e produzindo `IngestPayload`:

- **Reddit** (`app/ingest/reddit.py`):
  - `fetch_reddit_post_browser(url)` — **método padrão** do CLI (`--url`). Usa
    Camoufox (Firefox stealth) pra renderizar a página e faz scraping do
    componente `<shreddit-post>`. Não precisa de credenciais.
  - `fetch_reddit_post(url)` — PRAW/OAuth, via `--praw`. Bloqueado hoje (app
    OAuth não registrado no Reddit).
  - `fetch_from_fixture(path)` — via `--fixture`, pra dev/testes.
- **Medium** (`app/ingest/medium.py`): via mirror Freedium (`fetch_web_article`),
  contorna bloqueio de Cloudflare/paywall.

Ambos convergem no mesmo contrato `IngestPayload` (`app/schemas.py`), então o
módulo LLM não precisa saber de onde o conteúdo veio.

**Se algum scraper quebrar no futuro** (mudança de layout do Reddit, Freedium
sair do ar, etc.), o ponto de ajuste é isolado dentro de `app/ingest/*` — o
contrato `IngestPayload` não muda, então o resto da pipeline (LLM, TTS,
render) não é afetado.

---

## 2. Decisões Tomadas

| Decisão | Escolha | Motivo |
|---|---|---|
| Providers de LLM | **Multi-provider desde o início**: OpenAI + Gemini | Evitar lock-in, permitir trocar/comparar custo e qualidade |
| Abstração | Interface comum (`LLMProvider` ou similar) por trás de um factory/config | Trocar provider = mudar config, não código de negócio |
| Output | **Structured output obrigatório** (JSON Schema / function calling / tool use nativo do provider) | Não fazer parsing frágil de string livre |
| Dev sem API key | Stub determinístico (sem chamada de rede) | Permite testar o pipeline completo sem custo/API key |
| Async | **Não** na orquestração (Celery já roda 1 job por vez, sequencial). **Sim, pontual**, só pra chamadas de I/O paralelizáveis dentro de uma etapa (ex: gerar N imagens complementares ao mesmo tempo via `ThreadPoolExecutor`/`asyncio.gather`) | Async na orquestração adiciona complexidade (event loop dentro de worker sync do Celery) sem ganho — não é servidor de alta concorrência. O ganho real de paralelismo está em chamadas de API independentes all dentro de uma etapa (geração de imagens) |
| Escopo da LLM | A LLM **só gera o roteiro** (`hook`, `narration_lines`, `visual_prompts` descritivos, `target_duration_seconds`). **Não decide** qual imagem original vai em qual linha — isso é lógica determinística depois | Mais barato (sem tokens gastos em decisão mecânica), mais previsível/testável sem precisar mockar IA pra testar o matching |
| Prioridade de imagens | 1º usa `media_urls` do post original (na ordem em que aparecem); só aciona geração via IA (Leonardo/Stability) pros segmentos que sobrarem sem imagem | Já era a decisão do PLAN.md (Módulo 4b) — imagens reais > geradas quando disponíveis |
| Duração do vídeo | **Em aberto por enquanto** — sem trava rígida na LLM. Foco em manter simples primeiro | Regra de negócio futura (ex: mínimo ~2min pra monetização) é tratada como exceção depois, não trava o design agora |

---

## 3. MVP Simplificado — Post de Imagem (prioridade atual)

**Pivô de escopo (2026-07-23):** antes de atacar o pipeline de vídeo completo
(TTS, FFmpeg, render), vamos validar as ideias com um MVP bem mais simples e
rápido de entregar: **post de imagem(ns) pro Instagram**, sem vídeo.

```
IngestPayload → [LLM: gera legenda longa + texto curto p/ imagem]
             → [Processamento de imagem: crop 4:5 + marca d'água + texto overlay na 1ª imagem]
             → post pronto (imagens processadas + legenda)
```

Isso vira o **novo Módulo 3 prioritário**; o `VideoScript` (seção 5) fica
como Fase 2, depois de validar esse fluxo mais simples.

### 3.1 Exemplo de referência (input real)

Post usado como teste: [r/autohospedagem — "Meu backup vault"](https://www.reddit.com/r/autohospedagem/comments/1upwsyg/).
Exemplo de legenda "de sucesso" (estilo a replicar):

> 🖥️ Um entusiasta do Reddit construiu um servidor de backup doméstico capaz de
> acomodar até **84 HDs SAS de 3 TB**, **20 SSDs SAS de 400 GB** para cache e
> aproximadamente **260 TB de capacidade bruta** — e ele fica desligado quase
> o mês inteiro! 😮
>
> ⚡ O fluxo é 100% automatizado: o servidor acorda via Wake-on-LAN, sincroniza
> apenas os arquivos novos ou modificados, verifica a integridade dos dados e
> envia um relatório por e-mail e Discord antes de desligar sozinho. O
> conceito de *cold storage* nunca foi tão prático!
>
> 🔧 O hardware reutiliza um PC pessoal com Ryzen 7 5700X3D... (specs)
>
> 💬 O que você acharia de ter uma estrutura dessas em casa? Deixa sua opinião
> nos comentários!
>
> #homelab #coldstorage #backup #servidordomestico #tecnologia

Padrão observado: gancho com emoji → explicação → detalhes técnicos → CTA de
engajamento → hashtags. É esse padrão que o prompt da LLM deve reproduzir.

### 3.2 Duas saídas de texto distintas (confirmado)

- **`post_caption`**: legenda longa (o texto acima), publicada junto do post.
- **`image_caption`**: texto curto (gancho/título) sobreposto na **1ª imagem**
  do carrossel — é a primeira coisa vista no feed, então precisa ser
  autoexplicativo mesmo sem ler a legenda.

### 3.3 Regras de imagem (confirmadas)

| Regra | Decisão |
|---|---|
| Plataforma/formato alvo | Instagram feed/carrossel, proporção **4:5** (1080×1350) |
| Origem das imagens | `media_urls` do `IngestPayload` (prioridade); geração via IA só se faltar |
| Marca d'água | Placeholder/imagem aleatória por enquanto (asset real fica pra depois) |
| Onde aplicar marca d'água | **Só na 1ª imagem** do carrossel (por enquanto) |
| Onde aplicar `image_caption` | **Só na 1ª imagem** do carrossel |
| Demais imagens do carrossel | Só crop/resize pra 4:5, sem overlay nem marca d'água |
| Lib de processamento | Pillow (a confirmar ao implementar — é a escolha natural em Python, leve, sem dependências pesadas) |

### 3.4 Padrão visual da sobreposição (template fixo, confirmado)

Aplica-se **só à 1ª imagem** do carrossel. Padronizado pra manter identidade
visual consistente entre posts — não é decisão por post, é template fixo no
código:

| Elemento | Especificação |
|---|---|
| Posição do `image_caption` | **Terço inferior** da imagem (não cobre o centro, onde geralmente está o assunto principal da foto) |
| Legibilidade | Faixa/gradiente escuro semitransparente atrás do texto (de transparente no topo do terço até mais escuro na base da imagem) |
| Fonte | Sans-serif bold genérica e gratuita (ex: Montserrat Bold / Poppins Bold — a definir arquivo `.ttf` exato na implementação) |
| Cor do texto | Branco, sem itálico/decoração extra |
| Tamanho do texto | Não trunca no código — a **LLM já gera o `image_caption` curto** (prompt com limite de ~60-80 caracteres); o código só faz quebra de linha automática (`textwrap`) se ainda assim não couber numa linha |
| Alinhamento do texto | Esquerda |
| **Padding (texto)** | **5px** de distância das bordas |
| Marca d'água — posição | **Canto superior direito** |
| Marca d'água — tamanho | Pequena e discreta (~10-15% da largura da imagem) |
| **Marca d'água — opacidade** | **70%** |
| **Padding (marca d'água)** | **5px** de distância das bordas |
| Ordem de composição | 1) imagem base (crop 4:5) → 2) faixa/gradiente inferior → 3) texto → 4) marca d'água (por cima de tudo, canto superior direito) |

> **Nota de implementação:** padding e opacidade acima devem virar **constantes
> nomeadas** no topo do módulo (`TEXT_PADDING_PX = 5`, `WATERMARK_OPACITY = 0.7`,
> etc.), não valores soltos no meio da lógica — o usuário já sinalizou que
> pode querer ajustar esses números depois, então precisam ser fáceis de
> encontrar e mudar sem caçar no código.

### 3.5 Plano de implementação — Fase A (este MVP)

1. `app/schemas.py` — novo schema `ImagePost` (`post_caption: str`, `image_caption: str`, talvez `hashtags: list[str]` separado ou embutido no `post_caption`).
2. `app/llm/` — mesma abstração multi-provider da seção 2, mas gerando `ImagePost` em vez de `VideoScript` por ora. Prompt deve instruir explicitamente o limite de caracteres do `image_caption` (seção 3.4).
3. `app/media/image_post.py` (novo) — Pillow: download da imagem original, crop/resize 4:5, faixa/gradiente + overlay de texto (1ª imagem), composição da marca d'água (1ª imagem), seguindo o template da seção 3.4.
4. CLI: encadear `ingest → llm → image_post`, saída em `data/ingested/{source}/.../post/` com imagens prontas + `caption.txt`.
5. Testes: LLM mockada (sem rede) + testes de processamento de imagem com Pillow (fixtures de imagem pequena).

`VideoScript`/TTS/FFmpeg ficam para depois de validar esse fluxo simples.

---

## 4. Decisões Pendentes / Perguntas em Aberto

- [ ] **Qual API do Gemini usar?** (Google AI Studio / `google-genai` SDK vs Vertex AI)
- [ ] **Qual API da OpenAI?** (Chat Completions com `response_format=json_schema` vs Responses API)
- [ ] **Nome/forma da abstração**: uma classe `Protocol` Python simples? Ou algo mais formal (ex: LiteLLM como dependência, que já unifica múltiplos providers)?
- [ ] **Estratégia de fallback**: se o provider A falhar (rate limit, erro), tenta o B automaticamente? Ou é só escolha manual via config?
- [ ] **Few-shot examples**: vêm de onde? Arquivos fixture com exemplos "de sucesso" (Reddit/Medium) versionados no repo?
- [ ] **Retry em erro de schema**: reenviar prompt com o erro de validação Pydantic anexado? Quantas tentativas?
- [ ] **Cost tracking**: registrar tokens/custo por chamada desde já, ou deixar pro Módulo de Orquestração (Postgres `job_steps.cost_cents`)?
- [ ] **Idioma do roteiro**: sempre pt-BR (conteúdo de entrada é majoritariamente pt-BR), ou detectar idioma do post original?
- [ ] **Matching imagem↔narração**: regra exata do algoritmo determinístico (round-robin em ordem de aparição no post? 1 imagem por narration_line sempre, ou pode repetir/pular?). A definir junto com o Módulo 4b/5 (Media/Render).
- [ ] **Duração do vídeo**: sem trava por ora. Retomar quando o Módulo 5 (Render) estiver em desenho — nesse ponto decidir se impomos faixa/mínimo (ex: 2min p/ monetização) ou deixamos a LLM livre.

### 4.1 Pendências levantadas na revisão de código do usuário (2026-07-24)

- [ ] **Comentários do post no `IngestPayload`**: adicionar campo (ex: `top_comments: list[str]`) pra dar mais contexto à IA. Exige mudar `app/ingest/reddit.py` pra extrair os top comentários, além do post.
- [ ] **Prompt em voz de redator/jornalista, não 1ª pessoa**: a IA deve escrever como quem está transformando conteúdo de terceiros em legenda, não como se fosse o dono do post. Ajuste no `PROMPT_TEMPLATE` (`app/llm/prompts.py`).
- [ ] **Emojis no texto sobreposto (`apply_caption_text`)**: a fonte Montserrat não tem glyphs de emoji. Precisaria de fonte de emoji separada (ex: Noto Color Emoji) composta por cima do texto.
- [ ] **Centralizar `image_caption`**: hoje é left-aligned (decisão da seção 3.4). Reavaliar se centralizado fica melhor esteticamente.
- [ ] **Margem de segurança no texto**: o texto não deve tocar NENHUMA borda (esquerda/direita/inferior), não só o padding padrão de 5px do padrão visual. Ajustar `apply_caption_text` pra reservar uma margem própria além do `TEXT_PADDING_PX`.
- [ ] **Gradiente mais forte**: `GRADIENT_MAX_OPACITY` (hoje 190/255) pode estar sutil demais; considerar aumentar.
- [ ] **Crop "burro" corta trecho errado quando a IA (ex: Reddit) dá zoom em algo fora do centro**: `crop_to_canvas` hoje é sempre centralizado. Considerar crop inteligente (focal point / detecção de sujeito) no futuro.
- [ ] **Geração de imagem via IA quando não há `media_urls`**: já é o Módulo 4b do `PLAN.md` (fallback Leonardo.ai/Stability Diffusion) — revisar/planejar com mais detalhe quando chegar a hora.

---

## 5. Contrato de Saída (Fase 2, futuro): `VideoScript`

> Fica pausado até validarmos o MVP de imagem (seção 3). Mantido aqui só
> pra não perder o desenho já discutido.

```python
class VideoScript(BaseModel):
    hook: str                          # primeiros segundos, gancho de atenção
    narration_lines: list[str]          # frases da narração, em ordem
    visual_prompts: list[str]           # 1 prompt de imagem por narration_line (ou por segmento)
    target_duration_seconds: int
```

Ainda não implementado em `app/schemas.py`. Pontos a confirmar antes de
implementar: `visual_prompts` é 1:1 com `narration_lines`, ou pode ter cardinalidade
diferente (ex: 1 imagem a cada N segundos, não por frase)?

---

## 6. Plano de Implementação — Fase 2 (vídeo completo, futuro)

1. `app/schemas.py` — adicionar `VideoScript`.
2. `app/llm/` (novo pacote):
   - `base.py` — interface/Protocol comum (`generate_script(payload) -> VideoScript`).
   - `openai_provider.py`, `gemini_provider.py` — implementações.
   - `stub.py` — gera `VideoScript` determinístico a partir do `IngestPayload`, sem rede.
   - `factory.py` — escolhe provider via env var (`LLM_PROVIDER=openai|gemini|stub`).
3. Testes com providers mockados (sem chamada de rede real), + teste do stub.
4. CLI: `python -m app script --from-json data/ingested/reddit/x.json` (ou encadeado direto após `ingest`).

---

## 7. Log de Sessões

- **2026-07-23**: Implementado `fetch_reddit_post_browser` (Camoufox) como método
  padrão do CLI Reddit, contornando bloqueio de app OAuth não registrado.
  Testado e-2-e com post real. 36 testes passando. Iniciado planejamento do
  Módulo LLM (este doc).
- **2026-07-23 (cont.)**: Discussão de escopo do módulo LLM — definido que async
  não é necessário na orquestração (só paralelismo pontual em geração de
  imagens), que a LLM só gera roteiro (não decide matching de imagem), que
  imagens originais do post têm prioridade sobre geração via IA, e que a
  duração do vídeo fica em aberto por ora (foco em manter simples).
- **2026-07-23 (cont. 2)**: Pivô de escopo — priorizar um MVP mais simples
  antes do vídeo completo: post de imagem(ns) pro Instagram (legenda longa +
  texto curto sobreposto na 1ª imagem + marca d'água placeholder + crop 4:5).
  Vídeo/TTS/FFmpeg viram Fase 2. Seção 3 detalha o novo desenho.
- **2026-07-24**: Implementação da Fase A concluída e validada e2e:
  - `ImagePost` schema, `app/llm/` (stub/Gemini/OpenAI + factory + prompt
    compartilhado), `app/config.py` (`LLMConfig`/`load_llm_config`).
  - Corrigido bug de duplicação em `gemini_provider.py`; testado ao vivo com
    a chave real do usuário contra o fixture do Reddit — saída seguiu bem o
    estilo hook/contexto/detalhes/CTA/hashtags do exemplo de referência.
  - `app/media/image_post.py` — Pillow: crop 4:5 (1080x1350), gradiente
    inferior, texto (Montserrat Bold via variable font, 5px padding), marca
    d'água (canto superior direito, 70% opacidade, 5px padding) — todos os
    valores como constantes nomeadas no topo do arquivo. Fonte Montserrat
    baixada para `app/media/assets/fonts/`; watermark placeholder gerado em
    `app/media/assets/watermark/`.
  - Novo subcomando `python -m app image-post --ingest-json <path>` encadeia
    ingest salvo → LLM → carrossel de imagens + `caption.txt` em
    `data/posts/{stem}/`. Testado e2e com imagens reais do Reddit.
  - 15 novos testes (`tests/test_llm.py`, `tests/test_image_post.py`), sem
    chamadas de rede reais (stub + mocks + imagens geradas em memória).
    Suite completa: 51 testes passando.
  - `requirements.txt` atualizado com `pillow`, `google-genai`, `openai`.
  - Próximos passos (não bloqueantes): decidir few-shot/retry/cost-tracking
    (seção 4, ainda em aberto).

