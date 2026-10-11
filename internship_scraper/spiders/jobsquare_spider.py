import re
from datetime import datetime

import scrapy


def clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


class JobsquareSpider(scrapy.Spider):
    name = "jobsquare"
    allowed_domains = ["jobsquare.ma"]
    start_urls = ["https://www.jobsquare.ma/categories/2021/informatique-technologies-jobs/"]
    max_pages = 3  # pages de liste par categorie

    STAGE_RE = re.compile(r"\b(stage|stagiaire|pfe|intern|internship)\b", re.I)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.seen_ids = set()
        self.seen_categories = set()
        self.discovered = False

    def parse(self, response, page=1, category=None):
        base_url = response.url.split("?")[0]
        if category is None:
            category = base_url.rstrip("/").split("/")[-1]
            self.seen_categories.add(base_url)

        # Web Structure : on decouvre les autres categories via les liens du menu
        if not self.discovered:
            self.discovered = True
            for href in response.css("a::attr(href)").getall():
                url = response.urljoin(href).split("?")[0]
                if re.search(r"/categories/\d+/", url) and url not in self.seen_categories:
                    self.seen_categories.add(url)
                    yield response.follow(url, callback=self.parse)

        for card in response.css("div.sj-job-card"):
            job_id = card.attrib.get("id")
            if not job_id or job_id in self.seen_ids:
                continue

            title = clean(card.css(".sj-card-title a::text").get())
            contract = clean(card.css(".sj-type::text").get())
            if not (self.STAGE_RE.search(contract) or self.STAGE_RE.search(title)):
                continue  # on garde uniquement les stages

            self.seen_ids.add(job_id)
            yield {
                "source": "jobsquare.ma",
                "offer_id": job_id,
                "url": card.css(".sj-card-title a::attr(href)").get(),
                "title": title,
                "company": clean(card.css(".sj-card-company a::text").get()),
                "location": clean(card.css(".sj-loc::text").get()),
                "contract": contract,
                "description": clean(" ".join(card.css(".sj-card-desc::text").getall())),
                "date_text": clean(card.css(".sj-card-date::text").get()),
                # Web Structure : d'ou vient l'offre dans le site
                "category": category,
                "listing_page": page,
                "scraped_at": datetime.now().isoformat(timespec="seconds"),
            }

        if page < self.max_pages:
            yield response.follow(
                f"{base_url}?page={page + 1}",
                callback=self.parse,
                cb_kwargs={"page": page + 1, "category": category},
            )