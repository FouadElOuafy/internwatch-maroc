import re
from datetime import datetime

import scrapy


class DreamjobSpider(scrapy.Spider):
    name = "dreamjob"
    allowed_domains = ["dreamjob.ma"]
    start_urls = ["https://www.dreamjob.ma/stage/"]
    max_pages = 3  # nombre de pages de liste a parcourir

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.seen = set()

    def parse(self, response, page=1):
        for art in response.css("article.jeg_post"):
            url = art.css("h3.jeg_post_title a::attr(href)").get()
            if not url or url in self.seen:
                continue
            self.seen.add(url)
            title = art.css("h3.jeg_post_title a::text").get(default="").strip()
            date_text = " ".join(art.css(".jeg_meta_date a::text").getall()).strip()
            yield response.follow(
                url,
                callback=self.parse_offer,
                cb_kwargs={"title": title, "date_text": date_text, "page": page},
            )

        # Pagination : /stage/page/2/, /stage/page/3/ ...
        if page < self.max_pages:
            yield response.follow(
                f"/stage/page/{page + 1}/",
                callback=self.parse,
                cb_kwargs={"page": page + 1},
            )

    def parse_offer(self, response, title, date_text, page):
        texts = response.xpath(
            "//div[contains(@class,'content-inner')]//text()"
            "[not(ancestor::script) and not(ancestor::style)]"
        ).getall()
        content = re.sub(r"\s+", " ", " ".join(texts)).strip()
        content = re.sub(r"\s*Source:\s*Source\s*$", "", content)

        # On ignore les publicites et formations payantes deguisees en offres
        if re.search(r"ne s.agit pas d.une offre d.emploi", content, re.I):
            return

        links = response.css("div.content-inner a::attr(href)").getall()

        yield {
            "source": "dreamjob.ma",
            "offer_id": response.url.rstrip("/").split("/")[-1],  # cle unique
            "url": response.url,
            "title": title,
            "date_text": date_text,
            "content": content,
            # Web Structure : liens sortants de l'offre et page de liste d'origine
            "nb_links": len(links),
            "listing_page": page,
            "scraped_at": datetime.now().isoformat(timespec="seconds"),
        }