"""Captura productos JSON que la web pública de Alcampo carga al navegar.

Instala dependencias con ``python -m pip install -r requirements.txt`` y
``python -m playwright install chromium``. Ejecuta ``python scripts/alcampo.py``.
El script no construye peticiones API protegidas: usa la web con Chromium y
guarda las respuestas JSON de catálogo que la propia página solicita.
"""

import argparse
import asyncio
from collections import deque
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from urllib.parse import urlsplit, urlunsplit


SITE_ORIGIN = "https://www.compraonline.alcampo.es"
API_PATH_PREFIX = "/api/webproductpagews/"
UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def walk_product_objects(value):
    """Recorre una estructura JSON y devuelve objetos con identificador de producto."""
    if isinstance(value, dict):
        product_id = value.get("productId")
        if isinstance(product_id, str) and product_id:
            yield value
        for child in value.values():
            yield from walk_product_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_product_objects(child)


def walk_group_ids(value):
    """Extrae UUID referenciados por listas ``products`` de grupos de catálogo."""
    if isinstance(value, dict):
        products = value.get("products")
        if isinstance(products, list):
            for product_id in products:
                if isinstance(product_id, str) and UUID_PATTERN.fullmatch(product_id):
                    yield product_id
        for child in value.values():
            yield from walk_group_ids(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_group_ids(child)


def canonical_url(url):
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


class CatalogCapture:
    def __init__(self):
        self.products = {}
        self.product_ids = set()
        self.categories = {}
        self.api_responses = []
        self.errors = []
        self.blocked = None

    def add_product(self, product):
        product_id = product.get("productId")
        if not isinstance(product_id, str) or not product_id:
            return
        self.product_ids.add(product_id)
        existing = self.products.setdefault(product_id, {})
        existing.update(product)

    def add_state(self, url, state):
        if not isinstance(state, dict):
            return

        data = state.get("data")
        if not isinstance(data, dict):
            return
        product_data = data.get("products")
        if not isinstance(product_data, dict):
            return
        catalogue_state = product_data.get("catalogue")
        if not isinstance(catalogue_state, dict):
            return
        catalogue = catalogue_state.get("data", {})
        if not isinstance(catalogue, dict):
            return
        category = catalogue.get("currentCategory", {})
        if not isinstance(category, dict):
            category = {}
        group_ids = set(walk_group_ids(catalogue.get("productGroups", [])))

        for product in walk_product_objects(product_data):
            self.add_product(product)
            group_ids.add(product["productId"])
        self.product_ids.update(group_ids)

        if category or group_ids:
            record = self.categories.setdefault(
                url,
                {
                    "url": url,
                    "nombre": None,
                    "retailer_category_id": None,
                    "productos_declarados_en_categoria": None,
                    "total_products_listado": None,
                    "product_ids": set(),
                },
            )
            record["nombre"] = category.get("name", record["nombre"])
            record["retailer_category_id"] = category.get(
                "retailerCategoryId", record["retailer_category_id"]
            )
            record["productos_declarados_en_categoria"] = category.get(
                "productCount", record["productos_declarados_en_categoria"]
            )
            record["total_products_listado"] = catalogue.get(
                "totalProducts", record["total_products_listado"]
            )
            record["product_ids"].update(group_ids)

    async def add_api_response(self, response, category_url):
        from playwright.async_api import Error as PlaywrightError

        parsed = urlsplit(response.url)
        if (
            parsed.netloc != urlsplit(SITE_ORIGIN).netloc
            or not parsed.path.startswith(API_PATH_PREFIX)
        ):
            return
        if response.status in (401, 403, 429):
            self.blocked = (
                f"HTTP {response.status} al cargar la API pública de catálogo"
            )

        content_type = response.headers.get("content-type", "")
        record = {
            "url": response.url,
            "metodo": response.request.method,
            "http_status": response.status,
            "content_type": content_type,
            "categoria_url": category_url,
        }
        if response.status < 200 or response.status >= 300:
            record["error"] = "La respuesta HTTP no fue exitosa"
            self.api_responses.append(record)
            return
        if "json" not in content_type.lower():
            record["error"] = "La respuesta no declara contenido JSON"
            self.api_responses.append(record)
            return

        try:
            body = await response.json()
        except (PlaywrightError, ValueError, UnicodeDecodeError) as error:
            record["error"] = f"No se pudo interpretar el JSON: {error}"
            self.api_responses.append(record)
            return

        record["json"] = body
        self.api_responses.append(record)
        for product in walk_product_objects(body):
            self.add_product(product)


def save_capture(folder, capture, started_at, requested_categories, capped):
    categories = []
    for category in capture.categories.values():
        item = dict(category)
        item["product_ids"] = sorted(item["product_ids"])
        item["productos_detallados_capturados"] = sum(
            product_id in capture.products for product_id in item["product_ids"]
        )
        categories.append(item)

    products = {
        "capturado_utc": datetime.now(timezone.utc).isoformat(),
        "fuente": SITE_ORIGIN,
        "nota_cobertura": (
            "Captura de navegación pública; puede estar limitada por la tienda, "
            "el catálogo visible o la paginación de Alcampo."
        ),
        "categorias": categories,
        "product_ids_unicos": sorted(capture.product_ids),
        "productos_detallados": sorted(
            capture.products.values(), key=lambda product: product.get("productId", "")
        ),
    }
    with (folder / "productos_alcampo.json").open("x", encoding="utf-8") as file:
        json.dump(products, file, ensure_ascii=False, indent=2)

    with (folder / "respuestas_api.json").open("x", encoding="utf-8") as file:
        json.dump(capture.api_responses, file, ensure_ascii=False, indent=2)

    metadata = {
        "inicio_utc": started_at.isoformat(),
        "fin_utc": datetime.now(timezone.utc).isoformat(),
        "estado": (
            "incompleta"
            if capture.blocked or capture.errors or capped
            else "captura_concluida_no_certifica_catalogo_completo"
        ),
        "url_inicial": requested_categories[0] if requested_categories else SITE_ORIGIN,
        "paginas_visitadas": len(requested_categories),
        "categorias_visitadas": sum(
            urlsplit(url).path.startswith("/categories/")
            for url in requested_categories
        ),
        "categorias_con_datos": len(categories),
        "identificadores_unicos": len(capture.product_ids),
        "productos_detallados": len(capture.products),
        "respuestas_api": len(capture.api_responses),
        "limite_de_categorias_alcanzado": capped,
        "acceso_restringido": capture.blocked,
        "errores": capture.errors,
    }
    with (folder / "metadata.json").open("x", encoding="utf-8") as file:
        json.dump(metadata, file, ensure_ascii=False, indent=2)


async def capture_site(args, capture):
    from playwright.async_api import Error as PlaywrightError
    from playwright.async_api import async_playwright

    response_tasks = set()

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=not args.headed and not args.manual
        )
        page = await browser.new_page()

        def on_response(response):
            task = asyncio.create_task(
                capture.add_api_response(
                    response,
                    canonical_url(page.url)
                    if urlsplit(page.url).path.startswith("/categories/")
                    else None,
                )
            )
            response_tasks.add(task)
            task.add_done_callback(response_tasks.discard)

        page.on("response", on_response)
        queue = deque([canonical_url(args.url)])
        queued = set(queue)
        visited = []
        category_count = 0
        capped = False

        try:
            if args.manual:
                try:
                    await page.goto(
                        args.url, wait_until="domcontentloaded", timeout=45000
                    )
                except PlaywrightError as error:
                    capture.errors.append({"url": args.url, "error": str(error)})

                print(
                    "Navega en Chromium por las categorías que quieras incluir y "
                    "desplázate para cargar productos. Pulsa Enter aquí al terminar."
                )
                input_task = asyncio.create_task(
                    asyncio.to_thread(input, "Capturando; pulsa Enter para finalizar: ")
                )
                seen_pages = set()
                while not input_task.done():
                    current_url = canonical_url(page.url)
                    current_path = urlsplit(current_url).path
                    if (
                        urlsplit(current_url).netloc == urlsplit(SITE_ORIGIN).netloc
                        and (
                            current_path in ("", "/")
                            or current_path.startswith("/categories/")
                        )
                    ):
                        if current_url not in seen_pages:
                            visited.append(current_url)
                            seen_pages.add(current_url)
                        if current_path.startswith("/categories/"):
                            try:
                                state = await page.evaluate(
                                    "() => window.__INITIAL_STATE__ || null"
                                )
                                capture.add_state(current_url, state)
                            except PlaywrightError as error:
                                capture.errors.append(
                                    {"url": current_url, "error": str(error)}
                                )
                    await page.wait_for_timeout(1000)
                try:
                    await input_task
                except EOFError:
                    capture.errors.append(
                        {"error": "La entrada terminó antes de finalizar la captura manual."}
                    )
                current_url = canonical_url(page.url)
                if (
                    current_url not in seen_pages
                    and urlsplit(current_url).path.startswith("/categories/")
                ):
                    visited.append(current_url)
                    state = await page.evaluate(
                        "() => window.__INITIAL_STATE__ || null"
                    )
                    capture.add_state(current_url, state)
                queue.clear()

            while queue and not capture.blocked:
                url = queue.popleft()
                parsed = urlsplit(url)
                is_category = parsed.path.startswith("/categories/")
                if is_category and args.max_categories and category_count >= args.max_categories:
                    capped = True
                    break
                if is_category:
                    category_count += 1

                try:
                    response = await page.goto(
                        url, wait_until="domcontentloaded", timeout=45000
                    )
                except PlaywrightError as error:
                    capture.errors.append({"url": url, "error": str(error)})
                    continue
                visited.append(url)

                if response and response.status in (401, 403, 429):
                    capture.blocked = f"HTTP {response.status} al abrir {url}"
                    break
                if response and response.status >= 400:
                    capture.errors.append(
                        {"url": url, "error": f"HTTP {response.status}"}
                    )
                    continue

                await page.wait_for_timeout(1800)

                idle_rounds = 0
                previous_snapshot = None
                for _ in range(args.max_scrolls):
                    if capture.blocked:
                        break
                    try:
                        state = await page.evaluate(
                            "() => window.__INITIAL_STATE__ || null"
                        )
                        capture.add_state(url, state)
                        hrefs = await page.locator(
                            "a[href*='/categories/']"
                        ).evaluate_all("(elements) => elements.map(element => element.href)")
                        snapshot = await page.evaluate(
                            "() => ({height: document.body.scrollHeight, "
                            "products: document.querySelectorAll(\"a[href*='/products/']\").length})"
                        )
                    except PlaywrightError as error:
                        capture.errors.append({"url": url, "error": str(error)})
                        break

                    for href in hrefs:
                        target = canonical_url(href)
                        target_parts = urlsplit(target)
                        if (
                            target_parts.netloc == urlsplit(SITE_ORIGIN).netloc
                            and target_parts.path.startswith("/categories/")
                            and target not in queued
                        ):
                            queue.append(target)
                            queued.add(target)

                    current_snapshot = (
                        snapshot.get("height"),
                        snapshot.get("products"),
                        len(capture.api_responses),
                    )
                    if current_snapshot == previous_snapshot:
                        idle_rounds += 1
                    else:
                        idle_rounds = 0
                    if idle_rounds >= 4:
                        break
                    previous_snapshot = current_snapshot
                    await page.evaluate(
                        "() => window.scrollTo(0, document.body.scrollHeight)"
                    )
                    await page.wait_for_timeout(args.scroll_wait_ms)

                if response_tasks:
                    await asyncio.gather(*tuple(response_tasks))
                if queue and args.delay_seconds:
                    await asyncio.sleep(args.delay_seconds)
        finally:
            if response_tasks:
                await asyncio.gather(*tuple(response_tasks))
            await browser.close()

    return visited, capped


async def main():
    parser = argparse.ArgumentParser(
        description="Captura el JSON de productos cargado por la web pública de Alcampo."
    )
    parser.add_argument(
        "--url",
        default=SITE_ORIGIN,
        help="Inicio del recorrido; por defecto, la portada de Alcampo.",
    )
    parser.add_argument(
        "--max-categories",
        type=int,
        default=0,
        help="Máximo de categorías (0 = sin límite).",
    )
    parser.add_argument(
        "--max-scrolls",
        type=int,
        default=80,
        help="Máximos desplazamientos por página para activar carga progresiva.",
    )
    parser.add_argument(
        "--scroll-wait-ms",
        type=int,
        default=900,
        help="Espera entre desplazamientos.",
    )
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=1.0,
        help="Pausa entre páginas de categoría.",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Abre Chromium visible para revisar la navegación.",
    )
    parser.add_argument(
        "--manual",
        action="store_true",
        help="Permite navegar y desplazarse manualmente en Chromium visible.",
    )
    args = parser.parse_args()

    parsed_url = urlsplit(args.url)
    if (
        parsed_url.scheme != "https"
        or parsed_url.netloc != urlsplit(SITE_ORIGIN).netloc
        or (
            parsed_url.path not in ("", "/")
            and not parsed_url.path.startswith("/categories/")
        )
    ):
        parser.error(f"--url debe pertenecer a {SITE_ORIGIN}")
    if args.max_categories < 0 or args.max_scrolls < 1 or args.scroll_wait_ms < 0:
        parser.error("Los límites deben ser positivos (max-categories admite 0).")
    if args.delay_seconds < 0:
        parser.error("--delay-seconds no puede ser negativo.")

    started_at = datetime.now(timezone.utc)
    output_folder = (
        Path(__file__).resolve().parent
        / "data"
        / "raw"
        / started_at.strftime("%Y-%m-%dT%H-%M-%S-%fZ")
    )
    output_folder.mkdir(parents=True, exist_ok=False)

    capture = CatalogCapture()
    visited = []
    capped = False
    try:
        visited, capped = await capture_site(args, capture)
    except ImportError as error:
        capture.errors.append(
            {
                "error": (
                    "Falta Playwright. Instala requirements.txt y ejecuta "
                    "'python -m playwright install chromium'."
                ),
                "detalle": str(error),
            }
        )
    except OSError as error:
        capture.errors.append({"error": str(error)})
    finally:
        save_capture(output_folder, capture, started_at, visited, capped)

    print(f"Productos y UUID: {output_folder / 'productos_alcampo.json'}")
    print(f"Respuestas de red: {output_folder / 'respuestas_api.json'}")
    print(f"Resumen: {output_folder / 'metadata.json'}")
    if capture.blocked:
        print(f"Restricción detectada en el servidor: {capture.blocked}")
    if capture.errors:
        print(f"Se registraron {len(capture.errors)} errores; consulta metadata.json.")
    return 1 if capture.blocked or capture.errors or capped else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))