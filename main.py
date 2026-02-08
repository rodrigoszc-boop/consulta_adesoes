import asyncio
import base64
import json
import re
from datetime import datetime as dt, timedelta as td
from typing import Dict, List, Optional, Tuple
import aiohttp
import folium
import streamlit as st
from streamlit_folium import st_folium

API_URL = "https://dadosabertos.compras.gov.br/modulo-arp/2_consultarARPItem"
MAX_CONCURRENCY = 4
DATE_RANGE_DAYS = 360
PAGE_SIZE = {"Material": 100, "Serviço": 100}
BR_GEOJSON_PATH = "brasil-estados.geojson"
BR_MUN_GEOJSON_PATH = "brasil-municipios.geojson"
FEEDBACK_FORM_URL = "https://docs.google.com/forms/d/e/1FAIpQLSezbh3FqKnysgDFlC62kncVgndl2ie2nyYswDF55QcBPDtAqA/viewform?usp=publish-editor"


st.set_page_config(
    page_title="Buscador de Adesões",
    page_icon="⚓",
    layout="wide",
)

CUSTOM_CSS = """
<style>
    .main {
        background: radial-gradient(120% 120% at 0% 0%, #0f172a 0%, #0b1220 45%, #0a0f1d 100%);
        color: #e2e8f0;
        font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
    }
    .block-container {
        padding: 2rem 3rem 4rem 3rem;
    }
    .stSelectbox label, .stSlider label, .stTextInput label {
        font-weight: 600;
        color: #e2e8f0;
    }
    .metric-card {
        background: #11182b;
        border: 1px solid #1f2937;
        border-radius: 12px;
        padding: 1rem 1.25rem;
        box-shadow: 0 10px 30px rgba(0,0,0,0.35);
    }
    .result-card {
        background: #0f1627;
        border-radius: 10px;
        padding: 0.9rem 1.1rem;
        margin-bottom: 0.55rem;
        border: 1px solid #1f2937;
        box-shadow: 0 6px 24px rgba(0,0,0,0.28);
    }
    a {
        color: #76c7ff !important;
        text-decoration: none !important;
        font-weight: 600;
    }
    a:hover {
        color: #a3d9ff !important;
        text-decoration: underline !important;
    }
    .status-text {
        color: #cbd5e1;
        font-size: 0.95rem;
        margin-bottom: 0.25rem;
    }
    .stFolium,
    .stFolium iframe,
    .folium-map,
    iframe[title="streamlit_folium.st_folium"],
    iframe[title^="streamlit_folium"],
    div[data-testid="stIframe"],
    div[data-testid="stIframe"] iframe {
        height: 650px !important;
        min-height: 650px !important;
        width: 100% !important;
    }
    .feedback-link {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        gap: 6px;
        background: #0f1627;
        border: 1px solid #1f2937;
        border-radius: 18px;
        padding: 0.45rem 0.85rem;
        color: #e2e8f0 !important;
        font-weight: 700;
        white-space: nowrap;
        box-shadow: 0 10px 30px rgba(0,0,0,0.35);
    }
    .feedback-link:hover {
        background: #111d34;
        color: #ffffff !important;
    }
</style>
"""


@st.cache_data
def load_catalog(path: str) -> Dict[str, str]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_ata_url(identifier: str) -> str:
    """Monta a URL da ata a partir do identificador retornado pela API."""
    try:
        orgao = identifier.split("-")[0]
        compra = identifier.split("/")[1].split("-")[0]
        year = identifier.split("-")[2].split("/")[0].lstrip("0") or "0"
        ata = identifier.split("-")[-1].split("/")[0].lstrip("0") or "0"
        arquivo = identifier.split("-")[1]
        return (
            f"https://pncp.gov.br/pncp-api/v1/orgaos/{orgao}"
            f"/compras/{compra}/{year}/atas/{ata}/arquivos/{arquivo}"
        )
    except Exception:
        return ""


def normalize_item(item: Dict) -> Optional[Tuple[str, str, str, str, str]]:
    """Prepara dados da ata para exibição e evita entradas sem adesão."""
    if item.get("maximoAdesao", 0) == 0:
        return None

    numero_ata = item.get("numeroAtaRegistroPreco", "Ata não informada")
    unidade = item.get("nomeUnidadeGerenciadora", "Unidade não informada")
    fornecedor = item.get("nomeRazaoSocialFornecedor", "Fornecedor não informado")
    identificador = item.get("numeroControlePncpAta", "")
    url = build_ata_url(identificador)

    return numero_ata, unidade, fornecedor, identificador, url


def filter_results_by_uf(
    results: List[Dict], uasg_index: Dict[str, Dict[str, str]], uf: str
) -> List[Dict]:
    filtered: List[Dict] = []
    for raw in results:
        uasg_code = extract_uasg(raw)
        if not uasg_code:
            continue
        uasg_info = uasg_index.get(str(uasg_code))
        if not uasg_info:
            continue
        if uasg_info.get("siglaUf") == uf:
            filtered.append(raw)
    return filtered


def extract_uf_from_map(map_data: Optional[Dict]) -> Optional[str]:
    if not map_data:
        return None
    candidate = map_data.get("last_object_clicked")
    if isinstance(candidate, dict):
        props = candidate.get("properties", {})
        sigla = props.get("sigla") or candidate.get("sigla")
        if isinstance(sigla, str) and sigla:
            return sigla
    candidate = (
        map_data.get("last_object_clicked_popup")
        or map_data.get("last_object_clicked_tooltip")
    )
    if not candidate:
        return None
    text = str(candidate)
    text = re.sub(r"<[^>]+>", " ", text)
    match = re.search(r"\b[A-Z]{2}\b", text)
    return match.group(0) if match else None


def parse_remaining_pages(raw_value) -> int:
    """Garante que paginasRestantes seja tratado como inteiro seguro."""
    try:
        return max(int(raw_value or 0), 0)
    except (TypeError, ValueError):
        return 0


def extract_uasg(item: Dict) -> Optional[str]:
    """Tenta extrair o código UASG do item retornado pela API."""
    candidates = [
        "uasg",
        "codigoUasg",
        "codigoUnidadeGerenciadora",
        "codigoUnidadeGestora",
        "codigoUG",
    ]
    for key in candidates:
        value = item.get(key)
        if value:
            return str(value)
    return None


@st.cache_data
def load_uasg_index(path: str) -> Dict[str, Dict[str, str]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except UnicodeDecodeError:
        with open(path, "r", encoding="latin-1") as f:
            data = json.load(f)
    index: Dict[str, Dict[str, str]] = {}
    for entry in data:
        code = str(entry.get("codigoUasg", "")).strip()
        if not code:
            continue
        index[code] = entry
    return index


@st.cache_data
def load_state_geojson(path: str) -> Dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def polygon_centroid(coords: List[List[float]]) -> Tuple[float, float]:
    """Calcula o centróide aproximado de um polígono (lon/lat)."""
    if not coords:
        return 0.0, 0.0

    area = 0.0
    cx = 0.0
    cy = 0.0
    for idx in range(len(coords) - 1):
        x0, y0 = coords[idx]
        x1, y1 = coords[idx + 1]
        cross = x0 * y1 - x1 * y0
        area += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross

    if area == 0.0:
        xs = [pt[0] for pt in coords]
        ys = [pt[1] for pt in coords]
        return (sum(ys) / len(ys), sum(xs) / len(xs))

    area *= 0.5
    cx /= 6.0 * area
    cy /= 6.0 * area
    return (cy, cx)


def compute_state_centroids(geojson: Dict) -> Dict[str, Tuple[float, float]]:
    centroids: Dict[str, Tuple[float, float]] = {}
    for feature in geojson.get("features", []):
        sigla = feature.get("properties", {}).get("sigla")
        geometry = feature.get("geometry", {})
        if not sigla or not geometry:
            continue
        geom_type = geometry.get("type")
        coords = geometry.get("coordinates", [])
        if geom_type == "Polygon" and coords:
            centroid = polygon_centroid(coords[0])
        elif geom_type == "MultiPolygon" and coords:
            weighted_lat = 0.0
            weighted_lon = 0.0
            total_area = 0.0
            for polygon in coords:
                if not polygon:
                    continue
                ring = polygon[0]
                if len(ring) < 3:
                    continue
                area = 0.0
                for idx in range(len(ring) - 1):
                    x0, y0 = ring[idx]
                    x1, y1 = ring[idx + 1]
                    area += x0 * y1 - x1 * y0
                area = abs(area) / 2.0
                if area == 0.0:
                    continue
                lat, lon = polygon_centroid(ring)
                weighted_lat += lat * area
                weighted_lon += lon * area
                total_area += area
            if total_area == 0.0:
                centroid = (0.0, 0.0)
            else:
                centroid = (weighted_lat / total_area, weighted_lon / total_area)
        else:
            centroid = (0.0, 0.0)
        centroids[sigla] = centroid
    return centroids


@st.cache_data
def load_municipio_geojson(path: str) -> Dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@st.cache_data
def compute_municipio_centroids(geojson: Dict) -> Dict[str, Tuple[float, float]]:
    centroids: Dict[str, Tuple[float, float]] = {}
    for feature in geojson.get("features", []):
        props = feature.get("properties", {})
        mun_id = props.get("id")
        geometry = feature.get("geometry", {})
        if not mun_id or not geometry:
            continue
        geom_type = geometry.get("type")
        coords = geometry.get("coordinates", [])
        if geom_type == "Polygon" and coords:
            centroid = polygon_centroid(coords[0])
        elif geom_type == "MultiPolygon" and coords:
            weighted_lat = 0.0
            weighted_lon = 0.0
            total_area = 0.0
            for polygon in coords:
                if not polygon:
                    continue
                ring = polygon[0]
                if len(ring) < 3:
                    continue
                area = 0.0
                for idx in range(len(ring) - 1):
                    x0, y0 = ring[idx]
                    x1, y1 = ring[idx + 1]
                    area += x0 * y1 - x1 * y0
                area = abs(area) / 2.0
                if area == 0.0:
                    continue
                lat, lon = polygon_centroid(ring)
                weighted_lat += lat * area
                weighted_lon += lon * area
                total_area += area
            if total_area == 0.0:
                centroid = (0.0, 0.0)
            else:
                centroid = (weighted_lat / total_area, weighted_lon / total_area)
        else:
            centroid = (0.0, 0.0)
        centroids[str(mun_id)] = centroid
    return centroids


def get_feedback_link() -> str:
    """Retorna o link do formulário de feedback."""
    return FEEDBACK_FORM_URL


async def fetch_page(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    page: int,
    base_params: Dict[str, str],
) -> Dict:
    params = {**base_params, "pagina": page}
    retries = 10
    delay = 0.5
    async with semaphore:
        for attempt in range(retries):
            try:
                async with session.get(API_URL, params=params) as response:
                    response.raise_for_status()
                    return await response.json()
            except Exception:
                if attempt == retries - 1:
                    raise
                await asyncio.sleep(delay)
                delay *= 2


async def search_async(
    tipo: str,
    codigo: str,
    status_placeholder: st.delta_generator.DeltaGenerator,
    federal_only: bool,
    uasg_sphere: Dict[str, str],
    max_concurrency: int = MAX_CONCURRENCY,
) -> List[Dict]:
    """Executa a busca no intervalo configurado, retornando resultados conforme chegam."""
    timeout = aiohttp.ClientTimeout(total=10)
    semaphore = asyncio.Semaphore(max_concurrency)
    connector = aiohttp.TCPConnector(limit=None, ssl=False)

    seen = set()
    results: List[Dict] = []
    tasks: List[asyncio.Task] = []
    total_pages_count = 0
    processed_pages = 0

    def render_payload(payload: Dict) -> None:
        for raw in payload.get("resultado", []):
            uasg_code = extract_uasg(raw)
            if federal_only:
                if not uasg_code:
                    continue
                if uasg_sphere.get(str(uasg_code)) != "F":
                    continue

            normalized = normalize_item(raw)
            if not normalized:
                continue
            key = normalized[3]
            if key in seen:
                continue
            seen.add(key)
            results.append(raw)

    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        end_date = dt.today().date()
        start_date = end_date - td(days=DATE_RANGE_DAYS - 1)
        base_params: Dict[str, str] = {
            "tamanhoPagina": PAGE_SIZE.get(tipo, 120),
            "dataVigenciaInicialMin": start_date.strftime("%Y-%m-%d"),
            "dataVigenciaInicialMax": end_date.strftime("%Y-%m-%d"),
        }
        if tipo == "Material":
            base_params["codigoPdm"] = codigo
        else:
            base_params["codigoItem"] = codigo

        first_page = await fetch_page(session, semaphore, 1, base_params)
        total_pages_count = 1 + parse_remaining_pages(first_page.get("paginasRestantes"))
        render_payload(first_page)
        processed_pages = 1

        for page in range(2, total_pages_count + 1):
            tasks.append(asyncio.create_task(fetch_page(session, semaphore, page, base_params)))

        if total_pages_count == processed_pages:
            status_placeholder.success("Busca concluída.")
            return results

        for idx, task in enumerate(asyncio.as_completed(tasks), start=processed_pages + 1):
            try:
                payload = await task
                render_payload(payload)
                status_placeholder.info(f"Processando páginas ({idx}/{total_pages_count})…")
            except Exception:
                status_placeholder.warning(
                    "Falha ao carregar uma das páginas. Retentativa não disponível."
                )

        status_placeholder.success("Busca concluída.")
        return results


def run_search(
    tipo: str,
    codigo: str,
    federal_only: bool,
    uasg_sphere: Dict[str, str],
) -> List[Dict]:
    status_placeholder = st.empty()

    with st.spinner("Consultando dados, por favor aguarde um momento…"):
        try:
            results = asyncio.run(
                search_async(
                    tipo,
                    codigo,
                    status_placeholder,
                    federal_only=federal_only,
                    uasg_sphere=uasg_sphere,
                )
            )
        except Exception:
            status_placeholder.error(
                "Não foi possível concluir a consulta agora, provavelmente por instabilidades no Compras.gov. Tente novamente em instantes."
            )
            return []

    return results


def build_map(results: List[Dict], uasg_index: Dict[str, Dict[str, str]]) -> folium.Map:
    geojson = load_state_geojson(BR_GEOJSON_PATH)
    counts_by_uf: Dict[str, int] = {}

    for raw in results:
        uasg_code = extract_uasg(raw)
        if not uasg_code:
            continue
        uasg_info = uasg_index.get(str(uasg_code))
        if not uasg_info:
            continue
        uf = uasg_info.get("siglaUf")
        if not uf:
            continue
        counts_by_uf[uf] = counts_by_uf.get(uf, 0) + 1

    mapa = folium.Map(
        location=[-14.235, -51.9253],
        zoom_start=4,
        tiles=None,
        height="650px",
        width="100%",
    )
    max_count = max(counts_by_uf.values(), default=0)

    try:
        import branca.colormap as cm

        colormap = cm.linear.YlGnBu_09.scale(0, max(max_count, 1))
        colormap.caption = "Atas encontradas"
        colormap.add_to(mapa)
        default_fill = "#e2e8f0"
    except Exception:
        colormap = None
        default_fill = "#1f2937"

    geojson_with_counts = json.loads(json.dumps(geojson))
    for feature in geojson_with_counts.get("features", []):
        sigla = feature.get("properties", {}).get("sigla")
        feature["properties"]["count"] = counts_by_uf.get(sigla, 0)

    def style_fn(feature: Dict) -> Dict[str, object]:
        sigla = feature.get("properties", {}).get("sigla")
        count = counts_by_uf.get(sigla, 0)
        fill_color = default_fill
        if colormap and count > 0:
            fill_color = colormap(count)
        return {
            "fillColor": fill_color,
            "color": "#1f2937",
            "weight": 1,
            "fillOpacity": 0.72 if count > 0 else 0.3,
        }

    folium.GeoJson(
        geojson_with_counts,
        style_function=style_fn,
        highlight_function=lambda _: {"weight": 2, "color": "#111827"},
        popup=folium.GeoJsonPopup(fields=["sigla"], labels=False),
        tooltip=folium.GeoJsonTooltip(
            fields=["name", "sigla", "count"],
            aliases=["Estado", "UF", "Atas"],
            localize=True,
        ),
    ).add_to(mapa)

    try:
        bounds = folium.GeoJson(geojson).get_bounds()
        mapa.fit_bounds(bounds, padding=(20, 20))
        mapa.options["maxBounds"] = bounds
        mapa.options["minZoom"] = 4
    except Exception:
        pass

    return mapa


with open("acanto.png", "rb") as f:
    acanto = f.read()
acanto = base64.b64encode(acanto).decode()


def main() -> None:
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

    feedback_link = get_feedback_link()
    st.markdown(
        f"""
        <div style="display:flex; justify-content:flex-end; margin-top:6px; margin-bottom:6px;">
            <a class="feedback-link" href="{feedback_link}" target="_blank" rel="noopener noreferrer">Sugestões, reclamações, pedidos ou elogios</a>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        f"""
    <div style="display: flex; align-items: center; gap: 12px;">
        <img src="data:image/png;base64,{acanto}" style="height: 2em;">
        <h1 style="margin: 0;">Buscador de Adesões 2.0</h1>
    </div>
    """,
        unsafe_allow_html=True,
    )
    st.caption("Consulta inteligente às atas de registro de preços do Compras.gov.br.")

    st.write(
        "Selecione o tipo de item, escolha o código desejado e encontre atas para adesão."
    )

    tipo = st.selectbox(
        "Tipo de item",
        ["Material", "Serviço"],
        index=None,
        placeholder="Selecione material ou serviço",
    )

    selected_label = None
    codigo = None
    federal_only = False
    uasg_sphere: Dict[str, str] = {}

    if tipo == "Material":
        materiais = load_catalog("catalogo_pdm.json")
        selected_label = st.selectbox(
            "Material",
            sorted(materiais.keys()),
            index=None,
            placeholder="Pesquise pelo nome do material",
        )
        if selected_label:
            codigo = materiais[selected_label]

    if tipo == "Serviço":
        servicos = load_catalog("catalogo_servicos.json")
        selected_label = st.selectbox(
            "Serviço",
            sorted(servicos.keys()),
            index=None,
            placeholder="Pesquise pelo nome do serviço",
        )
        if selected_label:
            codigo = servicos[selected_label]

    if selected_label:
        federal_only = st.checkbox("Buscar somente atas da esfera federal", value=True)
        if federal_only:
            uasg_sphere = load_catalog("esfera_uasg.json")

    st.session_state.setdefault("modo_exibicao", "Mapa")
    if "selected_uf" not in st.session_state:
        st.session_state["selected_uf"] = None
    next_mode = st.session_state.pop("next_modo_exibicao", None)
    if next_mode:
        st.session_state["modo_exibicao"] = next_mode
    if st.session_state.pop("reset_view", False):
        st.session_state["modo_exibicao"] = "Mapa"
        st.session_state["selected_uf"] = None


    start_button = st.button(
        "Buscar adesões", type="primary", use_container_width=True, disabled=not codigo
    )

    if start_button and tipo and codigo:
        results = run_search(tipo, codigo, federal_only, uasg_sphere)
        st.session_state["atas"] = results
        st.session_state["reset_view"] = True
    elif start_button and not codigo:
        st.warning("Selecione um item antes de iniciar a busca.")

    results = st.session_state.get("atas", [])
    modo_exibicao = st.session_state.get("modo_exibicao", "Mapa")
    selected_uf = st.session_state.get("selected_uf")
    if results:
        uasg_index = load_uasg_index("uasgs.json")
        if modo_exibicao == "Mapa":
            st.subheader("Mapa das atas encontradas")
            st.caption("Clique no estado onde deseja encontrar atas para adesão.")
            mapa = build_map(results, uasg_index)
            map_data = st_folium(
                mapa,
                height=650,
                use_container_width=True,
                returned_objects=[
                    "last_object_clicked",
                    "last_object_clicked_popup",
                    "last_object_clicked_tooltip",
                ],
            )
            clicked_uf = extract_uf_from_map(map_data)
            if clicked_uf and clicked_uf != selected_uf:
                st.session_state["selected_uf"] = clicked_uf
                st.session_state["next_modo_exibicao"] = "Lista"
                st.rerun()

        if modo_exibicao == "Lista":
            if selected_uf:
                st.subheader(f"Atas encontradas - {selected_uf}")
                if st.button("Limpar filtro de estado"):
                    st.session_state["selected_uf"] = None
                    st.session_state["next_modo_exibicao"] = "Mapa"
                    st.rerun()
                display_results = filter_results_by_uf(results, uasg_index, selected_uf)
            else:
                st.subheader("Atas encontradas")
                display_results = results

            if not display_results:
                st.info("Nenhuma ata encontrada para este estado.")
            else:
                for raw in display_results:
                    normalized = normalize_item(raw)
                    if not normalized:
                        continue
                    numero, unidade, fornecedor, _, url = normalized
                    st.markdown(
                        f"""
                        <div class="result-card">
                            <div class="status-text">Ata {numero} • {unidade}</div>
                            <div><a href="{url}" target="_blank">Visualizar documento – {fornecedor}</a></div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
    elif start_button and tipo and codigo:
        st.info("Nenhuma ata encontrada para este critério.")


if __name__ == "__main__":
    main()
