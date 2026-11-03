"""Primera ingesta: conserva respuestas originales y registra lo ocurrido.

Ejecutar: python fase_01_ingesta.py
Adapta RECURSOS a las URLs observadas y permitidas de la fuente.
El ejemplo descarga solo el índice y una subcategoría, no todo el catálogo.
"""

# datetime trabaja con fechas y horas; timezone permite indicar UTC.
from datetime import datetime, timezone
# json permite leer y escribir datos JSON.
import json
# Path representa rutas y permite crear carpetas o abrir archivos.
from pathlib import Path
# time.sleep introducirá una pausa entre peticiones.
import time

# requests es la librería externa que enviará las peticiones HTTP.
import requests


# Un diccionario relaciona claves y valores: nombre de salida -> URL de origen.
# Estas dos URLs de Mercadona sirven como ejemplo; compruébalas en Red/Network.
# Para otra fuente, sustituye las entradas por sus archivos y URLs.
RECURSOS = {
    "categorias.json": "https://tienda.mercadona.es/api/categories/",
    "categoria_36.json": "https://tienda.mercadona.es/api/categories/36/",
}


# def define una función; url y destino son los valores que recibe al llamarla.
def descargar(url, destino):
    """Recibe una URL y una ruta; guarda el cuerpo original y devuelve metadatos.

    Comprueba errores HTTP, respuestas vacías y JSON incorrecto. No limpia ni
    convierte los datos. Si falla, lanza una excepción que main() registra.
    """
    # GET solicita el recurso y devuelve un objeto Response. timeout limita las
    # esperas de conexión y de lectura; no fija la duración total de la descarga.
    respuesta = requests.get(url, timeout=30)
    # Lanza una excepción si el servidor responde con un error HTTP (4xx o 5xx).
    respuesta.raise_for_status()
    # content obtiene el cuerpo en bytes, sin convertirlo en texto ni en tabla.
    contenido = respuesta.content
    # Una secuencia vacía se evalúa como falsa; not detecta ese caso.
    if not contenido:
        # raise interrumpe la función y comunica el problema a quien la llamó.
        raise ValueError("La respuesta está vacía")

    # suffix obtiene la extensión; lower la pasa a minúsculas para compararla.
    if destino.suffix.lower() == ".json":
        json.loads(contenido)  # Comprueba el JSON sin reescribirlo.
    # elif comprueba esta alternativa solo si no se cumplió la condición anterior.
    elif destino.suffix.lower() == ".csv":
        # Las cabeceras describen la respuesta. get devuelve "" si falta esta clave.
        tipo = respuesta.headers.get("Content-Type", "").lower()
        # Detectamos HTML en vez de un CSV, como una página de acceso o de error.
        # lstrip quita espacios iniciales; startswith comprueba los prefijos.
        # b"..." representa bytes, el mismo tipo que tiene contenido.
        if "text/html" in tipo or contenido.lstrip().lower().startswith((b"<!doctype html", b"<html")):
            raise ValueError("Se esperaba un CSV, pero la web ha devuelto HTML")

    # x evita sobrescribir; b guarda bytes, sin transformar el cuerpo recibido.
    # with cierra el archivo al salir del bloque, incluso si se produce un error.
    with destino.open("xb") as archivo:
        # Escribimos el cuerpo recibido; no lo reconstruimos desde un JSON leído.
        archivo.write(contenido)

    # return entrega este diccionario a main(); no devuelve los datos descargados.
    return {
        # url es la dirección final, que puede cambiar si hubo redirecciones.
        "url": respuesta.url,
        # Guardamos estado HTTP, tipo declarado y tamaño para revisar la descarga.
        "http_status": respuesta.status_code,
        "content_type": respuesta.headers.get("Content-Type"),
        "bytes": len(contenido),
    }


# main agrupa la organización del proceso; el nombre es una convención de Python.
def main():
    """Crea una captura, recorre RECURSOS y guarda su resumen en metadata.json.

    Devuelve 0 si guardó todos los recursos configurados y 1 si hubo fallos.
    Completar estas URLs no significa haber obtenido todos los datos de la web.
    """
    # Guardamos la hora UTC para identificar la ejecución sin depender del huso local.
    inicio = datetime.now(timezone.utc)
    # __file__ es este script; resolve obtiene su ruta absoluta y parent su carpeta.
    base = Path(__file__).resolve().parent
    # En Path, / une partes de una ruta. strftime convierte la fecha en texto:
    # %Y-%m-%d = año-mes-día; %H-%M-%S = hora-minuto-segundo; %f = microsegundos.
    # La Z final indica UTC; los guiones evitan los dos puntos en nombres de Windows.
    carpeta = base / "data" / "raw" / inicio.strftime("%Y-%m-%dT%H-%M-%S-%fZ")
    # parents crea las carpetas intermedias; exist_ok=False falla si ya existe.
    carpeta.mkdir(parents=True, exist_ok=False)
    # Lista en la que guardaremos un diccionario de resultado por recurso intentado.
    registros = []

    # El finally exterior guardará el resumen al salir de este bloque.
    try:
        # items entrega pares (clave, valor); el for los separa en nombre y url.
        for nombre, url in RECURSOS.items():
            # Partimos de error y solo marcamos ok si descargar termina correctamente.
            registro = {"archivo": nombre, "url": url, "estado": "error"}
            # Este try controla los fallos de una descarga concreta.
            try:
                # Llamamos a descargar con la URL y la ruta de salida.
                # update incorpora al registro los metadatos que devuelve la función.
                registro.update(descargar(url, carpeta / nombre))
                registro["estado"] = "ok"
                # Una f-string inserta dentro del texto los valores entre llaves.
                print(f"Guardado: {carpeta / nombre}")
            # Capturamos fallos de red/HTTP, de validación y del sistema de archivos.
            # as error permite consultar la excepción y guardar su mensaje.
            except (requests.RequestException, ValueError, OSError) as error:
                registro["error"] = str(error)
                print(f"ERROR en {nombre}: {error}")
                # isinstance comprueba el tipo de error. and solo evalúa la segunda
                # condición si la primera es cierta. in comprueba la pertenencia.
                # 401: requiere autenticación; 403: acceso denegado; 429: límite de peticiones.
                if isinstance(error, requests.HTTPError) and error.response.status_code in (401, 403, 429):
                    print("Acceso restringido o demasiadas peticiones: detenemos la ingesta.")
                    # Salimos del bucle de recursos; los bloques finally se ejecutan.
                    break
            # Se ejecuta haya éxito o error, también antes de salir con break.
            finally:
                # append añade el resultado de este intento a la lista.
                registros.append(registro)
            time.sleep(1)  # Pausa entre peticiones; no garantiza permiso de acceso.
    finally:
        # También deja un registro si interrumpimos el proceso con Ctrl+C.
        # La expresión recorre los registros: cada comparación produce True o False.
        # sum los cuenta como 1 o 0 y obtiene el número de descargas correctas.
        correctos = sum(registro["estado"] == "ok" for registro in registros)
        # len cuenta elementos; isoformat convierte las fechas en texto normalizado.
        metadata = {
            "inicio_utc": inicio.isoformat(),
            "fin_utc": datetime.now(timezone.utc).isoformat(),
            # Expresión condicional: valor si se cumple la condición, else otro valor.
            "estado": "completa" if correctos == len(RECURSOS) else "incompleta",
            "recursos_previstos": len(RECURSOS),
            "archivos_guardados": correctos,
            "descargas": registros,
        }
        # x crea un archivo de texto sin sobrescribir; utf-8 conserva las tildes.
        with (carpeta / "metadata.json").open("x", encoding="utf-8") as archivo:
            # dump escribe el diccionario como JSON. ensure_ascii=False mantiene
            # caracteres como ñ; indent=2 añade sangría para facilitar la lectura.
            json.dump(metadata, archivo, ensure_ascii=False, indent=2)
        print(f"Resumen: {correctos}/{len(RECURSOS)} archivos. Carpeta: {carpeta}")

    # El código de salida será 0 si se guardó todo y 1 si faltó algún recurso.
    return 0 if correctos == len(RECURSOS) else 1


# Esta condición es cierta al ejecutar el archivo, no al importarlo desde otro.
if __name__ == "__main__":
    # Ejecuta main y entrega su resultado como código de salida a la terminal.
    raise SystemExit(main())