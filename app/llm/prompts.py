"""Shared prompt template for LLM providers generating ImagePost.

Kept separate from individual providers so gemini_provider.py and
openai_provider.py (and any future provider) build the exact same prompt --
provider modules should only differ in how they call their SDK and enforce
structured output, not in prompt wording.
"""

from app.llm.stub import IMAGE_CAPTION_MAX_CHARS
from app.schemas import IngestPayload

# Mirrors the caption style described in LLM_PLAN.md section 3.1: a hook
# with an emoji, context/explanation, concrete details, an engagement CTA,
# then hashtags. `image_caption` must stay short since it's overlaid on the
# image itself (see LLM_PLAN.md section 3.4) -- the code does not truncate
# it, so the limit has to be enforced here in the prompt.
PROMPT_TEMPLATE = """\
Você é um redator de mídias sociais especializado em transformar posts do \
Reddit e artigos em legendas para um carrossel de imagens do Instagram, em \
português do Brasil.

IMPORTANTE sobre a voz do texto: você está reescrevendo o conteúdo de outra \
pessoa para uma página que compartilha posts de terceiros -- você NÃO é o \
autor original. Escreva na perspectiva de um redator/curador contando a \
história de outra pessoa (ex: "Um usuário do Reddit construiu...", "O autor \
do post relata..."), e NUNCA em primeira pessoa como se você tivesse vivido \
a experiência (evite "Meu projeto", "Eu fiz", "Consegui finalmente").

Gere QUATRO saídas a partir do conteúdo abaixo:

1. `post_caption`: a legenda longa do post. Siga este padrão:
   - Gancho inicial com emoji, despertando curiosidade
   - Parágrafo de contexto/explicação
   - Detalhes/números concretos quando existirem no conteúdo original
   - Call-to-action de engajamento (pergunta pro público, convite a comentar)
   - 3 a 6 hashtags relevantes ao final
   Use parágrafos curtos separados por linha em branco. Não invente fatos que \
não estejam no conteúdo original.

2. `image_caption`: um texto CURTO (no máximo {max_chars} caracteres), tipo \
título/gancho, para ser sobreposto na primeira imagem do carrossel. Precisa \
fazer sentido sozinho, sem depender da legenda.

3. `visual_prompt`: um prompt CURTO e SIMPLES em INGLÊS (1-2 frases), descrevendo \
apenas uma foto de fundo com clima/atmosfera ligada ao tema geral do post \
(ex: tecnologia, ambiente doméstico, escritório, natureza) -- NÃO uma \
ilustração precisa do evento específico do post. Esse prompt só é usado \
quando o post original não tem nenhuma imagem própria, para gerar um fundo \
via IA (modelo de difusão gratuito, qualidade limitada). Regras:
   - Cena genérica e desfocada (bokeh, fora de foco), sem tentar retratar \
uma ação, objeto específico ou situação exata do post -- só o "clima" geral \
(ex: post sobre programação/IA → "a blurred laptop screen with soft code-like \
light reflections, dark moody office at night, bokeh, out of focus \
background"; post sobre um produto doméstico → "a blurred cozy home interior, \
warm ambient light, bokeh background").
   - Nada de close-ups ou objetos em primeiro plano nítido -- tudo desfocado, \
baixo detalhe. Isso evita deformações do modelo gratuito.
   - Sem pessoas, mãos, rostos, texto, logos ou marcas.
   - Estilo: "cinematic, moody lighting, shallow depth of field, out of \
focus, dark background" (ajuste a paleta de cor ao tom do post, mas mantenha \
sempre desfocado/simples).
   - Conteúdo seguro, cenário neutro e cotidiano.

4. `category`: escolha EXATAMENTE UMA das opções abaixo que melhor descreve \
o post (usada num selo/badge sobre a imagem):
   - CURIOSIDADE: fato ou situação inusitada, curiosa, surpreendente
   - TECNOLOGIA: sobre hardware, software, gadgets, tech em geral
   - TUTORIAL: passo a passo, guia, "como fazer"
   - HISTÓRIA: relato/experiência pessoal contada como história
   - DESABAFO: desabafo, reclamação, opinião emocional do autor original
   - NOTÍCIA: fato noticioso, anúncio, lançamento, atualização

Título original: {title}

Conteúdo original:
{content}
{comments_section}"""


def _build_comments_section(top_comments: list[str]) -> str:
    if not top_comments:
        return ""
    bullet_list = "\n".join(f"- {comment}" for comment in top_comments)
    return (
        "\nComentários mais votados (contexto extra, não cite diretamente, "
        f"use só para entender melhor a repercussão/reações ao post):\n{bullet_list}\n"
    )


def build_prompt(payload: IngestPayload) -> str:
    return PROMPT_TEMPLATE.format(
        max_chars=IMAGE_CAPTION_MAX_CHARS,
        title=payload.raw_title.strip() or "(sem título)",
        content=payload.raw_content.strip() or "(sem conteúdo)",
        comments_section=_build_comments_section(payload.top_comments),
    )
