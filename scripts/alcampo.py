"""Descarga una respuesta JSON del endpoint indicado de Alcampo.

Ejecutar desde la terminal: python alcampo.py
El script hace una sola petición y no intenta eludir restricciones de acceso.
"""

from datetime import datetime, timezone
import json
from pathlib import Path

from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


URL = (
    "https://www.compraonline.alcampo.es/api/webproductpagews/v5/"
    "product-pages?decoratedOnly=true&limit=27&tag=web&tag=lohp"
)


def descargar(url, destino):
    """Descarga el endpoint, valida el JSON y guarda el cuerpo original."""
    peticion = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(peticion, timeout=30) as respuesta:
            contenido = respuesta.read()
            url_final = respuesta.url
            http_status = respuesta.status
            content_type = respuesta.headers.get("Content-Type")
    except HTTPError as error:
        if error.code == 405:
            permitidos = error.headers.get("Allow", "no indicado")
            raise ValueError(
                f"HTTP 405: el endpoint no permite este método GET. "
                f"Métodos indicados por el servidor: {permitidos}"
            ) from error
        raise

    if not contenido:
        raise ValueError("La respuesta está vacía")

    datos = json.loads(contenido)

    with destino.open("xb") as archivo:
        archivo.write(contenido)

    return {
        "url_final": url_final,
        "http_status": http_status,
        "content_type": content_type,
        "bytes": len(contenido),
        "tipo_json": type(datos).__name__,
    }


def main():
    inicio = datetime.now(timezone.utc)
    base = Path(__file__).resolve().parent
    carpeta = base / "data" / "raw" / inicio.strftime("%Y-%m-%dT%H-%M-%S-%fZ")
    carpeta.mkdir(parents=True, exist_ok=False)

    registro = {"archivo": "productos_alcampo.json", "url": URL, "estado": "error"}
    try:
        registro.update(descargar(URL, carpeta / "productos_alcampo.json"))
        registro["estado"] = "ok"
        print(f"JSON guardado en: {carpeta / 'productos_alcampo.json'}")
    except (HTTPError, URLError, ValueError, OSError) as error:
        registro["error"] = str(error)
        print(f"No se pudo descargar el JSON: {error}")
        if isinstance(error, HTTPError):
            print("Métodos permitidos:", error.headers.get("Allow", "no indicado"))
            if error.code in (401, 403, 429):
                print("El servidor ha restringido la petición. Deteniendo la descarga.")

    metadata = {
        "inicio_utc": inicio.isoformat(),
        "fin_utc": datetime.now(timezone.utc).isoformat(),
        "estado": "completa" if registro["estado"] == "ok" else "incompleta",
        "descarga": registro,
    }
    with (carpeta / "metadata.json").open("x", encoding="utf-8") as archivo:
        json.dump(metadata, archivo, ensure_ascii=False, indent=2)

    return 0 if registro["estado"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
