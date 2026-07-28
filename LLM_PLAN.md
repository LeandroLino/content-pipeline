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
| Alinhamento do texto | **Centralizado** (atualizado em 2026-07-25; era esquerda na versão original do template, mudou após teste visual) |
| **Padding (texto)** | **5px** de distância das bordas + **margem de segurança extra** (`TEXT_SAFE_MARGIN_PX = 40px`) pra garantir que o texto nunca toque nenhuma borda, mesmo com quebra de linha |
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
- [x] **Estratégia de fallback** — implementado em 2026-07-25. `app/llm/factory.py`: se o provider selecionado (`LLM_PROVIDER`) falhar -- chave ausente OU erro em runtime (rede/rate-limit/auth) da chamada real ao SDK -- tenta automaticamente o outro provider real (gemini↔openai) que esteja configurado. Nunca cai silenciosamente pro `stub` (evita legendas de baixa qualidade irem pra produção sem ninguém perceber); se todos os providers reais falharem, levanta `LLMError` com o motivo de cada falha.
- [ ] **Few-shot examples**: vêm de onde? Arquivos fixture com exemplos "de sucesso" (Reddit/Medium) versionados no repo?
- [ ] **Retry em erro de schema**: reenviar prompt com o erro de validação Pydantic anexado? Quantas tentativas?
- [ ] **Cost tracking**: registrar tokens/custo por chamada desde já, ou deixar pro Módulo de Orquestração (Postgres `job_steps.cost_cents`)?
- [ ] **Idioma do roteiro**: sempre pt-BR (conteúdo de entrada é majoritariamente pt-BR), ou detectar idioma do post original?
- [ ] **Matching imagem↔narração**: regra exata do algoritmo determinístico (round-robin em ordem de aparição no post? 1 imagem por narration_line sempre, ou pode repetir/pular?). A definir junto com o Módulo 4b/5 (Media/Render).
- [ ] **Duração do vídeo**: sem trava por ora. Retomar quando o Módulo 5 (Render) estiver em desenho — nesse ponto decidir se impomos faixa/mínimo (ex: 2min p/ monetização) ou deixamos a LLM livre.

### 4.1 Pendências levantadas na revisão de código do usuário (2026-07-24)

- [x] **Comentários do post no `IngestPayload`** — implementado em 2026-07-25. Novo campo `top_comments: list[str]` no `IngestPayload`. `app/ingest/reddit.py` agora extrai os top comentários (por score) em todos os caminhos: PRAW (`_extract_top_comments_praw`), fixture JSON (`_extract_top_comments_from_listing`, lê o 2º elemento do Listing `[post, comments]`), e browser/Camoufox (`_extract_top_comments`, via `shreddit-comment[depth="0"]` + `div#{thingid}-comment-rtjson-content`). `app/llm/prompts.py` agora inclui os comentários (quando existirem) numa seção extra do prompt, como contexto adicional para a IA (não citar diretamente).
- [x] **Prompt em voz de redator/jornalista, não 1ª pessoa** — implementado em 2026-07-25. Testado ao vivo com Gemini: saída passou a usar 3ª pessoa ("um entusiasta...", "o autor..."), sem se apropriar da experiência do post.
- [x] **Emojis no texto sobreposto (`apply_caption_text`)** — implementado em 2026-07-25. A fonte Montserrat não tem glyphs de emoji, então adicionamos a fonte **Noto Color Emoji** (Google, gratuita, portátil entre SOs) em `app/media/assets/fonts/NotoColorEmoji.ttf`. Como essa fonte só tem um "strike" bitmap fixo (109px), os emojis são renderizados nesse tamanho nativo e depois redimensionados (via Pillow, `embedded_color=True`) para o tamanho visual da legenda. O texto é dividido em blocos (texto normal vs. emoji) por regex de faixas Unicode; cada linha é desenhada manualmente misturando os dois "fontes" (Montserrat pro texto branco, Noto Color Emoji pros emojis coloridos), mantendo a centralização e a margem de segurança já existentes. Caption sem nenhum emoji continua usando o caminho antigo (`multiline_text` com bbox exato), sem regressão.
- [x] **Centralizar `image_caption`** — implementado em 2026-07-25. `apply_caption_text` agora centraliza o bloco de texto horizontalmente (usando `multiline_textbbox`/`multiline_text` com `align="center"`), mantendo a margem de segurança em ambos os lados.
- [x] **Margem de segurança no texto** — implementado em 2026-07-25 junto com a correção do gradiente. Nova constante `TEXT_SAFE_MARGIN_PX = 40` reserva espaço extra além do `TEXT_PADDING_PX` padrão; o texto agora é ancorado usando o bounding box real da tinta (`multiline_textbbox`), não mais métricas de fonte aproximadas — corrige um bug em que o texto vazava ~10px além da margem calculada.
- [x] **Gradiente mais forte** — implementado em 2026-07-25. `GRADIENT_MAX_OPACITY` subiu de 190 para 225.
- [x] **Crop "burro" corta trecho errado quando a IA (ex: Reddit) dá zoom em algo fora do centro** — implementado em 2026-07-25. `crop_to_canvas` agora usa uma heurística leve de "energia de bordas" (`ImageFilter.FIND_EDGES` + `numpy`, sem depender de OpenCV/detecção de rosto): desloca a janela de corte pra onde há mais detalhe visual (bordas/textura), em vez de sempre cortar no centro geométrico. Pra imagens uniformes (sem "sujeito" identificável), o comportamento cai de volta pro crop centralizado de antes — sem regressão.
- [x] **Geração de imagem via IA quando não há `media_urls`** — implementado em 2026-07-25. Depois de testar Gemini (nano banana) e Leonardo.ai/Stability (todos exigem plano pago/créditos), optamos pelo **Pollinations.ai** (100% gratuito, sem API key, sem cartão). Mudanças: (1) `ImagePost` ganhou o campo `visual_prompt` (a LLM agora gera 3 saídas: `post_caption`, `image_caption`, `visual_prompt` -- este último em inglês, descrevendo a cena com base no post completo, usado só como fallback); (2) novo módulo `app/media/ai_image.py` (`generate_ai_image`) chama a API da Pollinations via HTTP GET simples; (3) `build_carousel` agora aceita `visual_prompt` opcional e gera uma imagem via IA quando `media_urls` está vazio (arquivo temporário é limpo depois de processado); (4) CLI (`python -m app image-post`) não trava mais quando o post não tem imagem -- avisa e segue com o fallback. Testado ao vivo: LLM stub + Pollinations.ai real geraram um carrossel completo e coerente com o texto do post.
  - **Ajuste em 2026-07-27** (após teste real revelar artefatos visuais, ex: ícone/UI alucinado na tela de um celular): descoberto que modelos de difusão **não lidam bem com negação** -- pedir "sem mãos"/"sem tela ligada"/"sem texto" no prompt tende a fazer esses elementos aparecerem mesmo assim, em vez de evitá-los. Reescrito `PROMPT_TEMPLATE` (seção do `visual_prompt`) e `app/llm/stub.py` para instruir/gerar **apenas descrições positivas** (ex: "celular repousando virado pra baixo sobre a mesa" em vez de "sem tela visível"; objetos sozinhos em vez de "sem mãos/pessoas"). Também removido o parâmetro `enhance=true` da chamada à Pollinations.ai em `app/media/ai_image.py` -- ele piorou a aderência ao prompt original em teste comparativo; mantido `model=flux` fixo (melhor fotorrealismo que `turbo`). Validado com teste end-to-end real (Gemini + Pollinations.ai): resultado sem ícones/UI alucinados, sem mãos, sem texto.
  - **2º ajuste no mesmo dia** (novo teste real revelou: celular deformado + cena genérica demais, sem relação clara com o tema "privacidade"): (1) instruções agora pedem que a cena inclua um **objeto simbólico concreto** que represente o conceito central do post (ex: privacidade → cadeado físico ao lado do celular), em vez de só "um objeto qualquer sozinho"; (2) para celular/laptop/eletrônicos, instrução para descrever a cena como **flat lay** (câmera direto de cima, objetos deitados) -- ângulos baixos/perspectiva dramática estavam deformando o aparelho (proporções erradas, formas duplicadas). Testado de novo com o mesmo post ("privacidade no smartphone"): celular renderizado sem deformação, cadeado/chave visíveis e tematicamente coerentes.
  - **3º ajuste no mesmo dia** (mais testes mostraram que objetos com detalhes finos/mecânicos -- teclados, fechaduras com o miolo à mostra -- ainda saem deformados no modelo gratuito; cotou-se testar Leonardo.ai como alternativa paga, mas a API exige créditos próprios separados do plano do app web -- não temos crédito de API ainda, então essa rota foi pausada, chave `LEONARDO_API_KEY` guardada no `.env` para o futuro). Em vez disso, **mudança de estratégia** inspirada num exemplo de referência (`data/example/post.jpg`, estilo TecMundo): a foto gerada por IA não precisa representar o tema com precisão simbólica -- pode ser apenas um **fundo desfocado/atmosférico** (bokeh, fora de foco, moody/cinematográfico), já que o significado visual do post vem do texto sobreposto (título) e da marca d'água, não da cena em si. `PROMPT_TEMPLATE` e `app/llm/stub.py` reescritos para pedir prompts curtos e simples (1-2 frases) descrevendo apenas clima/atmosfera genérica ligada ao domínio do post (ex: "a blurred laptop screen with soft neon light in a dark room, bokeh, cinematic"), sem tentar retratar objetos específicos em foco nítido. Testado com 2 posts diferentes (privacidade em smartphone, remoção de anúncios do Spotify): resultado consistente, sem deformações, visualmente alinhado ao estilo de referência.

### 4.2 Identidade visual padronizada ("boilerplate") — implementado em 2026-07-27

Depois de simplificar o `visual_prompt` (seção 4.1), o usuário adicionou 4 exemplos
de referência reais (`data/example/post.jpg` a `post4.jpg`, posts de páginas do
Instagram estilo TecMundo) e pediu pra adotar o mesmo template visual em todos os
posts, independente do fundo ser uma foto real (`media_urls`) ou gerada por IA.
Padrão identificado nos 4 exemplos: faixa **preta sólida** (não gradiente) na
base da imagem, um **selo/badge colorido de categoria**, título em **caixa alta**
logo abaixo do selo, uma linha de **CTA** menor/mais clara abaixo do título, e a
marca d'água/logo no canto superior direito (mantido como já estava).

Decisões confirmadas com o usuário:

| Elemento | Especificação |
|---|---|
| Categoria | Novo campo `category` no `ImagePost`, **gerado pela LLM** (4ª saída do prompt), tipado como `Literal[...]` com lista fixa: `CURIOSIDADE, TECNOLOGIA, TUTORIAL, HISTÓRIA, DESABAFO, NOTÍCIA` |
| Fonte do título | Mantida a Montserrat Bold já usada -- só passou a renderizar em **CAIXA ALTA** (`.upper()`), sem baixar fonte nova |
| Cor do selo/badge | Cor única de marca, fixa (não varia por categoria): **`#0E5294`** (RGB 14, 82, 148) -- extraída por amostragem de pixel de `data/example/post3.jpg` |
| Texto do CTA | String fixa **"Veja a legenda"**, sem seta/emoji, cor cinza-claro (`CTA_TEXT_COLOR`), fonte menor que o título |
| Logo/marca d'água | Mantido o asset já existente (`DEFAULT_WATERMARK_PATH`), sem mudanças |
| Faixa de fundo | Trocada de gradiente (`apply_gradient_overlay`, removida) para **retângulo preto 100% opaco** (`BAR_COLOR`), com altura calculada dinamicamente a partir do conteúdo real (badge + título quebrado em N linhas + CTA + margens), em vez de uma proporção fixa da imagem -- garante que títulos de 1 ou 3 linhas nunca fiquem cortados nem com espaço vazio excessivo |

Implementação (`app/media/image_post.py`): `apply_gradient_overlay` +
`_apply_caption_text_plain`/`_apply_caption_text_with_emoji` foram substituídos
por um único `apply_caption_text(img, text, category)` que desenha, de cima
pra baixo: (1) a barra preta opaca (altura calculada bottom-up), (2) o badge
de categoria (retângulo arredondado + texto em caixa alta), (3) o título
(caixa alta, múltiplas linhas, com o mesmo suporte a emoji colorido de antes),
(4) o texto do CTA. `build_carousel` ganhou um novo parâmetro obrigatório
`category: str` (logo após `image_caption`); `app/__main__.py` e todos os
testes foram atualizados para passar `image_post.category`. Testado ao vivo
com 2 posts (título de 3 linhas e de 1 linha) -- barra se ajusta corretamente
em ambos os casos, resultado visualmente alinhado aos 4 exemplos de referência.
Suíte completa: 73/73 testes passando após a mudança.

### 4.3 Ajuste: seta no CTA + faixa "dome" gradiente (não mais retângulo sólido) — implementado em 2026-07-27

Depois do primeiro teste real da seção 4.2, o usuário pediu dois ajustes: (1) o
CTA "Veja a legenda" precisava de uma seta (referência original tinha "⬇"); (2)
a faixa preta sólida ficou muito "pesada" -- pediu pra reduzir a opacidade ou,
alternativa preferida, estilizar como um **gradiente circular/dome** em vez de
um retângulo de bordas retas. Perguntado explicitamente, o usuário escolheu a
opção do gradiente circular.

- **Seta no CTA**: em vez de um emoji colorido (que exigiria a fonte
  Noto Color Emoji só para um caractere), usamos a seta unicode simples
  `↓` (U+2193, downwards arrow) -- confirmado que a própria Montserrat Bold já
  tem esse glyph, sem precisar de fonte extra. `CTA_TEXT = "Veja a legenda ↓"`.
- **Faixa "dome" gradiente**: `apply_gradient_overlay`/retângulo sólido
  substituído por `_dome_gradient_bar()`, que gera (via `numpy`) uma máscara
  alpha em forma de "domo": totalmente opaca (`BAR_MAX_OPACITY = 235`, não mais
  255 -- levemente translúcida mesmo no centro) do centro horizontal para baixo,
  com a borda superior da região opaca curvando pra baixo (ficando mais
  transparente) em direção às laterais -- deixa um pouco da foto aparecer nos
  cantos superiores da faixa, em vez de um corte reto. A transição usa
  suavização tipo smoothstep (`BAR_DOME_FEATHER_PX = 90`) ao invés de uma
  borda dura. Matematicamente: a curva de transição é a borda superior de uma
  elipse centrada abaixo do topo da faixa (`peak_y`), com semi-eixo horizontal
  = metade da largura do canvas e semi-eixo vertical = `BAR_DOME_HEIGHT_PX = 130`
  (controla o quão "arqueado" fica o domo). O badge/título/CTA continuam
  posicionados a partir do mesmo `bar_top`/`peak_y` de antes, então o texto
  sempre cai na parte já totalmente opaca do domo (a curva só afeta os cantos
  vazios acima do conteúdo). Testado ao vivo com os mesmos 2 posts da seção
  4.2 -- resultado com a curva visível e sutil, sem comprometer a legibilidade
  do texto. Suíte completa: 73/73 (1 teste reescrito para refletir a nova
  faixa em domo em vez do retângulo sólido).

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

