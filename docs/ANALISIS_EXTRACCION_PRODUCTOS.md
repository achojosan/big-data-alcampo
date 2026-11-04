# Análisis técnico: extracción de productos de Alcampo

**Objetivo:** desarrollar un scraper capaz de recorrer las categorías del supermercado online de Alcampo, obtener la información de sus productos y almacenarla en un dataset estructurado.

Durante la investigación hemos utilizado las herramientas de desarrollo de Chrome, analizado las peticiones HTTP de la aplicación y examinado el estado inicial de React.

## 1. Investigación de las peticiones HTTP

Comenzamos inspeccionando las solicitudes de red que realizaba la web al acceder a productos y categorías.

Identificamos este endpoint:

```http
PUT /api/webproductpagews/v6/products
```

Aunque habitualmente esperamos encontrar peticiones `GET` para recuperar información, comprobamos que Alcampo utiliza `PUT` para consultar detalles de productos.

### Hallazgos

- El cuerpo de la petición es un array JSON de identificadores UUID.
- Una única solicitud puede incluir varios productos.
- La respuesta devuelve un objeto JSON con un array `products`.
- La API proporciona información suficientemente estructurada para construir un dataset.

Por ejemplo, identificamos estos campos:

| Campo | Información |
| --- | --- |
| `productId` | Identificador UUID |
| `retailerProductId` | Referencia comercial |
| `name` | Nombre |
| `brand` | Marca |
| `price.amount` | Precio |
| `unitPrice.price.amount` | Precio unitario |
| `packSizeDescription` | Tamaño o cantidad |
| `available` | Disponibilidad |
| `categoryPath` | Categorías |
| `image.src` | Imagen principal |

**Conclusión:** no necesitamos extraer los textos individualmente de cada tarjeta HTML si podemos recuperar los objetos JSON.

## 2. Identificación del origen de los productos

El siguiente problema era descubrir cómo obtenía la aplicación los UUID.

Al navegar por categorías, observamos que:

- Al cargar la categoría no aparecía necesariamente una petición `product-pages`.
- Al hacer scroll aparecían nuevas peticiones `PUT /products`.
- Esas peticiones ya incluían los UUID de los productos que debían cargarse.

Esto planteó una pregunta: ¿cómo conoce la aplicación esos UUID antes de solicitar los detalles?

### Descubrimiento del estado inicial

Analizando el HTML completo de una categoría, encontramos:

```text
window.__INITIAL_STATE__
```

Es un objeto JavaScript que contiene datos utilizados para inicializar la aplicación React.

Dentro identificamos dos estructuras fundamentales:

```text
window.__INITIAL_STATE__
└── data
    └── products
        ├── catalogue
        │   └── data
        │       ├── productGroups
        │       ├── retailerCategoryId
        │       ├── currentCategory
        │       └── totalProducts
        │
        └── productEntities
```

**Conclusión:** la aplicación recibe en el HTML inicial los UUID de numerosos productos, junto con los datos completos de una parte de ellos.

Esto explica por qué no necesitamos encontrar una petición inicial `GET` que solicite todos los identificadores.

## 3. Comprobación en dos categorías distintas

Para verificar que el comportamiento no era exclusivo de una categoría, repetimos las pruebas en dos secciones diferentes.

| Dato | Conservas vegetales | Pescados, mariscos y surimis |
| --- | --- | --- |
| Identificador | OC100401 | OC1201 |
| Productos declarados | 375 | 289 |
| `totalProducts` | 282 | 237 |
| UUID únicos encontrados | 282 | 237 |
| Productos detallados inicialmente | 50 | 50 |
| Grupos de productos | 2 | 2 |

Observamos dos patrones consistentes.

### A. El número de UUID coincide con `totalProducts`.

En ambas categorías, todos los identificadores del listado inicial están disponibles sin necesidad de realizar peticiones adicionales.

### B. Solo hay 50 productos enriquecidos inicialmente.

Aunque conocemos los UUID de 237 o 282 productos, solo 50 aparecen inicialmente en `productEntities`.

Esto es compatible con una estrategia de carga diferida (lazy loading): la aplicación conoce los productos, pero recupera sus detalles a medida que los necesita.

### Diferencia entre los contadores

No debemos confundir:

- `currentCategory.productCount`: número de productos declarado para la categoría.
- `totalProducts`: número de productos del listado actual.

Todavía no hemos determinado por qué difieren. Podría deberse a disponibilidad, filtros u otros criterios comerciales.

Tampoco hemos comprobado qué ocurre en categorías con más de 300 productos.

## 4. Análisis de la estructura interna

Inspeccionamos los grupos mediante:

```javascript
const data = window.__INITIAL_STATE__.data.products;
const grupos = data.catalogue.data.productGroups;
```

Encontramos objetos como:

```javascript
{
  type: "on_offer",
  products: [
    "06617987-78c0-4c2c-a9c6-cf5a65d25ba3"
  ],
  name: "fop.headertitle.onoffer",
  clusterBreadcrumbs: [],
  additionalProductAttributes: []
}
```

Confirmamos que `products` contiene identificadores UUID, mientras que `productEntities` contiene objetos con información detallada.

Por ejemplo:

```javascript
{
  productId: "06617987-78c0-4c2c-a9c6-cf5a65d25ba3",
  retailerProductId: "67016",
  brand: "PESCANOVA",
  available: true
}
```

También descubrimos que los productos están organizados en grupos como `on_offer` y `ungrouped`.

**Conclusión:** podemos recuperar el inventario de identificadores directamente desde `productGroups` y relacionarlos con sus datos utilizando `productId`.

## 5. Intento de consultar directamente la API

Una vez obtenidos los UUID, intentamos consultar manualmente el endpoint desde la consola:

```javascript
fetch("/api/webproductpagews/v6/products", {
  method: "PUT",
  headers: {
    "Content-Type": "application/json",
    "Accept": "application/json"
  },
  credentials: "same-origin",
  body: JSON.stringify(ids.slice(0, 5))
});
```

La respuesta fue:

```text
403 Forbidden
Content-Type: null
```

Comprobamos que las peticiones realizadas correctamente por la propia aplicación incluían la cabecera:

```http
x-csrf-token: ...
```

Además, identificamos scripts relacionados con mecanismos de protección de solicitudes.

### Conclusión

La petición manual no cumple todos los requisitos necesarios para ser aceptada.

La ausencia del token CSRF es una posible explicación, pero no hemos demostrado que sea la única.

Por ahora, descartamos como estrategia principal reproducir manualmente solicitudes protegidas.

En su lugar, aprovecharemos las respuestas que genera la aplicación durante su funcionamiento normal.

## 6. Identificación de un segundo endpoint

Durante el análisis apareció:

```http
GET /api/webproductpagews/v6/product-pages
```

Este endpoint acepta parámetros como:

```text
includeAdditionalPageInfo=true
maxPageSize=300
maxProductsToDecorate=50
retailerCategoryId=OC13
```

Su respuesta contenía grupos de productos y un objeto `metadata` con `nextPageToken`.

Esto sugiere que existe un mecanismo de paginación basado en cursores.

Sin embargo, cuando inspeccionamos `window.__INITIAL_STATE__` en las categorías estudiadas, no encontramos esa propiedad de paginación.

**Conclusión:** sabemos que el endpoint `product-pages` puede devolver información de paginación, pero todavía no hemos establecido cómo se utiliza para recorrer todas las páginas de una categoría.

## 7. Arquitectura propuesta para el scraper

1. Acceder a una categoría

   Descargar el HTML inicial

2. Extraer `window.__INITIAL_STATE__`

   Recuperar UUID y productos detallados

3. Playwright

   Navegar y activar la carga progresiva

4. Capturar las respuestas `PUT`

   Obtener datos de los productos restantes

5. Normalizar y almacenar

   Eliminar duplicados y exportar CSV o SQLite

La ventaja de esta arquitectura es que no necesitamos construir manualmente las peticiones `PUT`: será la propia web la que las realice mientras Playwright captura las respuestas.

## 8. Estado actual del proyecto

| Funcionalidad | Estado |
| --- | --- |
| Identificar endpoint de detalle | Confirmado |
| Conocer estructura del JSON | Confirmado |
| Encontrar origen de los UUID | Confirmado |
| Extraer UUID de una categoría | Confirmado |
| Recuperar primeros 50 productos | Confirmado |
| Capturar respuestas `PUT` automáticamente | Pendiente |
| Obtener todos los detalles del listado | Pendiente |
| Resolver categorías grandes y paginación | Pendiente |
| Generar el dataset completo | Pendiente |

## Conclusión general

La investigación nos ha permitido pasar de intentar extraer información visualmente del HTML a comprender cómo la aplicación organiza y recupera los datos.

El hallazgo principal es que Alcampo separa los identificadores de los productos de sus datos detallados. En las dos categorías analizadas, proporciona todos los UUID del listado actual en el HTML inicial y enriquece inicialmente solo 50 productos.

La solución que mejor encaja con lo observado es combinar la extracción del estado inicial con la captura de las respuestas generadas durante la navegación normal mediante Playwright.
