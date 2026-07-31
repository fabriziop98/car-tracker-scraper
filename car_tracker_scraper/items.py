import scrapy


class ListingSummaryItem(scrapy.Item):
    """Un resultado de Discovery: lo que viene en la grilla de listado."""

    source = scrapy.Field()  # ej. "mercadolibre"
    source_listing_key = scrapy.Field()  # ej. "MLA1927083505" -> candidato a listing.source_listing_key
    url = scrapy.Field()  # URL canonica del aviso, usada por el Detail spider
    is_ad = scrapy.Field()  # bool, filtrar antes de persistir (no contaminar agregados)
    category_id = scrapy.Field()  # ej. "MLA1744"
    domain_id = scrapy.Field()  # ej. "MLA-CARS_AND_VANS" -> semilla candidata para el catalogo canonico
    title_raw = scrapy.Field()  # titulo libre, requiere normalizacion (normalization_alias)
    price_amount = scrapy.Field()
    price_currency = scrapy.Field()  # leer siempre este campo, nunca asumir por contexto de pagina
    attributes_raw = scrapy.Field()  # ej. ["2014", "184.000 Km"], parsear separador de miles y sufijo "Km"
    location_raw = scrapy.Field()  # ej. "Godoy Cruz, Mendoza"
    financing_initial_payment = scrapy.Field()  # anticipo de financiacion, si el aviso lo expone
    discovered_at = scrapy.Field()  # timestamp ISO de cuando el scraper lo vio


class ListingDetailItem(scrapy.Item):
    """El resultado de Detail Fetch: la ficha completa de un aviso."""

    source = scrapy.Field()
    source_listing_key = scrapy.Field()
    url = scrapy.Field()

    # De JSON-LD @type: Vehicle
    brand_raw = scrapy.Field()
    color = scrapy.Field()
    fuel_type_raw = scrapy.Field()
    number_of_doors = scrapy.Field()
    transmission_raw = scrapy.Field()
    item_condition = scrapy.Field()
    price_amount = scrapy.Field()
    price_currency = scrapy.Field()
    price_valid_until = scrapy.Field()
    breadcrumb_raw = scrapy.Field()  # ej. "Autos y Camionetas > Fiat > Palio" - semilla de catalogo

    # De __NORDIC_RENDERING_CTX__ / initialState.components
    subtitle_raw = scrapy.Field()  # ej. "2014 | 110.000 km - Publicado hace 1 ano"
    location_raw = scrapy.Field()
    highlighted_specs_raw = scrapy.Field()  # lista de pares key/value, ficha tecnica variable
    seller_name = scrapy.Field()
    seller_type = scrapy.Field()  # "car_dealer" | "particular" - viene explicito, sin heuristica
    seller_id = scrapy.Field()
    province_raw = scrapy.Field()
    item_status = scrapy.Field()
    financing_initial_payment = scrapy.Field()

    fetched_at = scrapy.Field()
