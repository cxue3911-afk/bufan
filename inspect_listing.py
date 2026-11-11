import asyncio
import sys
from playwright.async_api import async_playwright
from browser_runtime import browser_launch_options
from config import SITES
from batch_publish import _extract_links_js, DESKTOP_UA

async def main():
    key = sys.argv[1]
    site = SITES[key]
    async with async_playwright() as p:
        browser = await p.chromium.launch(**browser_launch_options())
        page = await browser.new_page(user_agent=DESKTOP_UA)
        await page.goto(site['listing_urls'][0], wait_until='domcontentloaded', timeout=60000)
        await page.wait_for_timeout(3000)
        links = await page.evaluate(_extract_links_js(), {'selector': site.get('selector',''), 'linkMatch': site.get('link_match','')})
        print('count', len(links))
        for link in links[:3]:
            loc = page.locator(f'a[href="{link["url"]}"]').first
            print('LINK', link)
            print((await loc.evaluate('(a) => a.parentElement.parentElement.parentElement.innerText'))[:500])
        await browser.close()

asyncio.run(main())
