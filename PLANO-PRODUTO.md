# Plataforma de Licitações — Plano de Produto e Instruções de Ingestão

> **Documento de handoff.** Escrito numa sessão do Claude Code na web (que só tinha acesso ao
> repositório `consulta_adesoes` e a rede com allowlist). Destina-se a ser entregue ao Claude Code
> rodando no **desktop**, que tem acesso às pastas locais (incl. o projeto "atas hunter" e o vault
> do Obsidian) e à internet sem allowlist. Leia tudo antes de começar.

---

## 1. Contexto

- O repositório `consulta_adesoes` é um **fork**. O app original é o "Buscador de Adesões 2.0",
  um app Streamlit que consulta atas de registro de preços para **adesão (carona)** no Compras.gov.br.
- A intenção **não** é continuar como app Streamlit, e sim usar esse fork como base para construir
  um **site/produto público** de licitações, onde a busca de adesões é apenas **uma** das
  funcionalidades (junto com busca de pregões/editais, atas, preços, fornecedores, etc.).
- Houve uma tentativa anterior (projeto **"atas hunter"**, no desktop) de baixar **todas** as atas
  do PNCP e filtrar depois — inviável (estimado em ~1 ano de download). Este documento corrige a
  abordagem.

### Como o app atual funciona (padrão a reaproveitar)

`main.py` no fork faz exatamente o padrão vencedor:
1. Descobre atas chamando o **Compras.gov dados abertos** (`dadosabertos.compras.gov.br/modulo-arp/2_consultarARPItem`),
   **filtrando por código de item** (`codigoPdm` para material, `codigoItem` para serviço).
2. Só depois monta a URL do documento no **PNCP** (`build_ata_url` → `pncp.gov.br/pncp-api/v1/...`).

A lógica async útil já existe: `fetch_page`, `search_async` (paginação + concorrência + retry/backoff).
Dados já presentes no repo:
- `catalogo_pdm.json` — nome do material → código PDM (14.532 itens).
- `catalogo_servicos.json` — nome do serviço → código.
- `uasgs.json` — código UASG → nome, UF, município/IBGE (21.498 unidades; **encoding latin-1**).
- `esfera_uasg.json` — UASG → esfera (F=federal, E=estadual, M=municipal).

---

## 2. Decisões de produto tomadas

- **Objetivo:** produto **público** (não ferramenta interna), com visual/identidade própria e SEO.
- **Stack de frontend:** disposto a usar **JavaScript** (frontend moderno, ex.: Next.js/React).
- **Amplitude:** **vertical de saúde agora, horizontal depois.** Começar focado em
  medicamentos/insumos de saúde (oncologia/alto custo como cunha de entrada), mas com modelo de
  dados genérico (qualquer CATMAT, qualquer esfera) para abrir para outros setores no futuro.
- **Cliente que paga:** **ainda indefinido.** Hipótese mais provável a validar primeiro =
  **fornecedores de saúde** (distribuidoras de medicamento/insumos) — ROI de vendas direto,
  orçamento privado, nicho menos saturado que "licitações em geral". Validar com 5–10 conversas
  antes de cravar.

### Por que não "licitações em geral"
Mercado horizontal já é lotado/maduro (Banco de Preços, Effecti, RadarLicita, Licitar Digital,
Painel de Preços). Entrar como "mais um buscador genérico" é difícil de diferenciar. Vertical de
saúde é mais defensável e tem fontes próprias (ver BPS abaixo).

---

## 3. Descoberta técnica — como achar atas por item em TODAS as esferas

Esta é a parte central. Existem **três camadas** com capacidades diferentes:

| Fonte | Filtra por item (CATMAT)? | Estadual/municipal? | Observação |
|---|---|---|---|
| **API de consulta PNCP** (`/v1/atas`, documentada) | ❌ Não — só `dataInicial`/`dataFinal` + `codigoModalidadeContratacao` | ✅ Sim | **Foi essa que o atas hunter usou** → obrigava "baixar tudo e filtrar". É o gargalo de ~1 ano. **NÃO usar para descoberta.** |
| **Busca do site PNCP** (`pncp.gov.br/api/search/`) | ✅ **Sim** — texto livre + CATMAT/CATSER + UF + esfera | ✅ Sim (tudo publicado desde 2023) | **É o que destrava o filtro por item em todas as esferas.** Pública, porém **não-documentada** (é o backend da barra de busca do site). |
| **Compras.gov dados abertos** | ✅ Sim | ❌ Quase só federal (SIASG) | É o que o `consulta_adesoes` usa hoje. Bom para o recorte federal. |

### O insight
Não usar a API de consulta documentada para descoberta. Usar a **API de busca do site do PNCP**
(`/api/search/`), que filtra por item/texto **na origem** e cobre federal + estadual + municipal.
Isso transforma "baixar tudo (1 ano)" em "buscar só o que interessa (minutos a horas)".

> **Atenção:** "busca no site" = chamar a **API JSON** `pncp.gov.br/api/search/` que o site usa por
> trás. **NÃO** é raspar o HTML. É a mesma busca da barra do site, consumida via HTTP/JSON.

### Fonte extra para o nicho saúde
**Banco de Preços em Saúde (BPS)** do Ministério da Saúde — base especializada em
medicamentos/produtos de saúde, incluindo compras **estaduais e municipais**. Vale integrar como
segunda fonte no vertical de saúde.

---

## 4. Arquitetura proposta

```
Frontend (Next.js / React)
  /adesoes  /pregoes  /atas  /precos  /fornecedores  /alertas  /dashboards
        │
        ▼
Backend (FastAPI)  ──►  camada de integração
        │                 ├─ PNCP search   (descoberta por CATMAT, todas as esferas)
        │                 ├─ PNCP API/arquivos (detalhe da ata, PDF)
        │                 ├─ Compras.gov   (federal)
        │                 └─ BPS           (saúde)
        ▼
Banco de dados (Postgres em prod / SQLite no MVP)
        └─ catálogos, UASGs, atas/itens/preços ingeridos, com checkpoint
```

**Princípio chave:** os três produtos possíveis (fornecedor / comprador / analista) rodam sobre os
**mesmos dados**. A diferença é só a camada de cima. Por isso a **camada de dados é o primeiro
passo** — ela é neutra em relação a quem vier a ser o cliente, e já resolve o problema original do
download.

**Decisão de arquitetura importante (ingestão vs. ao vivo):** o app atual bate na API ao vivo a cada
busca (lento e refém da instabilidade — o código tem retry e mensagem de "instabilidade do
Compras.gov"). Para um produto público, parte dos dados precisa ser **ingerida e guardada em banco**.
Modelar genérico (por CATMAT, qualquer esfera) mas **semear com saúde**.

---

## 5. Fluxo de ingestão (duas etapas)

```
1. DESCOBERTA  →  pncp.gov.br/api/search/
   - entrada: códigos/termos CATMAT (semente: oncológicos/alto custo de saúde)
   - filtra por item/texto + esfera + UF, na origem
   - saída: quais atas batem + metadados (órgão, UF, esfera, datas, identificador)

2. DETALHE     →  API documentada do PNCP / arquivos da ata
   - entrada: identificador vindo da etapa 1
   - saída: itens, preços, quantitativos, saldo p/ adesão, PDF
   - só nas atas que interessam (não em tudo)
```

---

## 6. Instruções para o Claude Code no desktop (próximos passos)

### Passo 0 — Pré-requisitos
- Você tem acesso ao projeto **"atas hunter"** local e ao **vault do Obsidian** com notas sobre o
  gargalo. **Leia-os primeiro** — havia código de download que precisa ser substituído/adaptado, e
  notas sobre por que ficou lento. (A sessão web NÃO teve acesso a eles.)
- Você tem internet **sem allowlist** → consegue bater nas APIs reais (a sessão web não conseguia:
  `pncp.gov.br`, `dadosabertos.compras.gov.br` e `compras.dados.gov.br` retornavam
  "Host not in allowlist").

### Passo 1 — Confirmar a API de busca do PNCP (CRÍTICO, fazer antes de codar de verdade)
A `pncp.gov.br/api/search/` é não-documentada; **confirme os parâmetros reais** batendo nela:
- Parâmetros a descobrir/validar: `q` (texto/CATMAT), `tipos_documento` (ex.: `ata`), paginação
  (`pagina`/`tam_pagina`), ordenação, e filtros de **esfera**, **UF**, **município**, **período**.
- Inspecione as chamadas de rede do site `https://www.pncp.gov.br` (DevTools → Network) ao buscar
  por um termo, e replique via `curl`/`httpx`.
- Documente o contrato real (campos de resposta: identificador da ata, órgão, UF, esfera, datas)
  num arquivo `docs/pncp-search-api.md`.

### Passo 2 — Montar a semente de CATMAT de saúde/oncologia
- Fonte: RENAME / protocolos do INCA / relação de antineoplásicos do SUS.
- Casar nomes de princípios ativos com códigos do `catalogo_pdm.json` (já no repo). Exemplos já
  confirmados no catálogo: CISPLATINA=5052, CARBOPLATINA=4520, PACLITAXEL=10279,
  DOXORRUBICINA=5168, DOCETAXEL=6376, GENCITABINA=5189, FLUORURACILA=14227, METOTREXATO=12176,
  RITUXIMABE=11761, TRASTUZUMABE=18503, IMATINIBE=1646/13788, CICLOFOSFAMIDA=4985,
  VINCRISTINA=13445, OXALIPLATINA=17818, IRINOTECANO=5195, TAMOXIFENO=5066, BEVACIZUMABE=18741.
- **Obs.:** não existe um PDM "oncológico" guarda-chuva — cada fármaco é um PDM próprio pelo
  princípio ativo. Curar uma lista de ~100–150 fármacos.

### Passo 3 — Escrever o ingestor (duas etapas, com checkpoint)
Estrutura sugerida (pode adaptar ao que já existe no atas hunter):
- `ingestor/discovery.py` — chama `pncp.gov.br/api/search/` por CATMAT/termo, pagina em paralelo
  (concorrência ~20–30 com semáforo + backoff; o atual usa só 4, conservador demais para batch),
  salva metadados das atas no banco. **Checkpoint** por código/data para retomar sem recomeçar.
- `ingestor/detail.py` — pega identificadores e busca o detalhe (itens/preços/PDF) na API do PNCP.
- `ingestor/db.py` — SQLite no MVP (schema: atas, itens, preços, órgãos/UASG, esfera, UF).
- Isolar a chamada da `/api/search/` num único módulo (ela pode mudar sem aviso).
- Reaproveitar o padrão async de `consulta_adesoes/main.py` (`fetch_page`, retry/backoff).

### Passo 4 — Expor via API (FastAPI)
- Endpoints neutros sobre o banco (`/atas`, `/atas/{id}`, `/precos`, `/fornecedores`), que servirão
  qualquer um dos produtos depois.

### Passo 5 (paralelo, não-código) — Validar cliente
- Conversas com fornecedores de saúde para confirmar/derrubar a hipótese de cliente pagante.

---

## 7. Riscos e ressalvas
- **`/api/search/` é não-documentada** → pode mudar sem aviso. Isolar e monitorar.
- **Cobertura temporal do PNCP**: dados a partir de ~2023. Para histórico federal mais antigo,
  complementar com Compras.gov.
- **Concorrência/rate limit**: ser educado (semáforo + backoff) para não ser bloqueado.
- **Qualidade de dados do PNCP** é irregular (há relatório público da Transparência Brasil sobre
  isso) — prever normalização/limpeza.
- **Mercado horizontal é concorrido** — manter o foco vertical no início.

---

## 8. Fontes
- Manual das APIs de Consultas PNCP — https://www.gov.br/pncp/pt-br/central-de-conteudo/manuais/versoes-anteriores/ManualPNCPAPIConsultasVerso1.0.pdf
- Swagger PNCP — https://pncp.gov.br/api/pncp/swagger-ui/index.html
- PNCP em Dados Abertos — https://www.gov.br/pncp/pt-br/acesso-a-informacao/dados-abertos
- GestGov: identificar contratações por CATMAT via API — https://gestgov.discourse.group/t/como-usar-a-api-de-dados-abertos-para-identificar-contratacoes-de-um-catmat-especifico/31856
- Manual API Compras.gov.br — https://www.gov.br/compras/pt-br/acesso-a-informacao/manuais/manual-dados-abertos/manual-api-compras.pdf
- Banco de Preços em Saúde (BPS) — https://economia.saude.bvs.br/
- Painel de Preços (federal) — https://paineldeprecos.planejamento.gov.br/
