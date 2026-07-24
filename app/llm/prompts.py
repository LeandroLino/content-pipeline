"""Shared prompt template for LLM providers generating ImagePost.

Kept separate from individual providers so gemini_provider.py and
openai_provider.py (and any future provider) build the exact same prompt --
provider modules should only differ in how they call their SDK and enforce
structured output, not in prompt wording.
"""

from app.llm.stub import IMAGE_CAPTION_MAX_CHARS
from app.schemas import IngestPayload

## Aprimorar prompt para que a IA gere legendas mais criativas e envolventes, talvez adicionar exemplos de legendas bem-sucedidas para inspirar a IA.
## IA deve estar em uma perspecitiva diferente, nao falar em primeira pessoa, mas sim como se fosse um redator ou jornalista de mídias sociais. nao estamos contando histórias pessoais, mas sim transformando conteúdo de terceiros em legendas envolventes.

# Mirrors the caption style described in LLM_PLAN.md section 3.1: a hook
# with an emoji, context/explanation, concrete details, an engagement CTA,
# then hashtags. `image_caption` must stay short since it's overlaid on the
# image itself (see LLM_PLAN.md section 3.4) -- the code does not truncate
# it, so the limit has to be enforced here in the prompt.
PROMPT_TEMPLATE = """\
Você é um redator de mídias sociais especializado em transformar posts do \
Reddit e artigos em legendas para um carrossel de imagens do Instagram, em \
português do Brasil.

Gere DUAS saídas de texto a partir do conteúdo abaixo:

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

Título original: {title}

Conteúdo original:
{content}
"""


def build_prompt(payload: IngestPayload) -> str:
    return PROMPT_TEMPLATE.format(
        max_chars=IMAGE_CAPTION_MAX_CHARS,
        title=payload.raw_title.strip() or "(sem título)",
        content=payload.raw_content.strip() or "(sem conteúdo)",
    )
