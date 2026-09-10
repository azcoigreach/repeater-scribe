import pytest
from playwright.sync_api import expect


@pytest.mark.parametrize(
    ("path", "title"),
    [
        ("/callsigns", "Callsigns | Repeater Scribe"),
        ("/callsigns/KM7GHS", "KM7GHS | Callsign History"),
    ],
)
def test_callsign_favicon_on_direct_load_reload_and_navigation(page, application, path, title):
    origin, _ = application

    def check_favicon():
        expect(page).to_have_title(title)
        icon = page.locator('head link[rel="icon"]')
        expect(icon).to_have_count(1)
        expect(icon).to_have_attribute("href", "/static/repeater-scribe-state0-256px.png")
        expect(icon).to_have_attribute("type", "image/png")
        response = page.request.get(origin + icon.get_attribute("href"))
        assert response.status == 200
        assert response.headers["content-type"] == "image/png"
        assert icon.evaluate("""async (link) => {
            const image = new Image();
            image.src = link.href;
            await image.decode();
            return [image.naturalWidth, image.naturalHeight];
        }""") == [256, 252]
        expect(page.locator(".brand-logo")).to_have_attribute("src", "/static/logo.png")

    # The page fixture creates a fresh context for each route, without favicon cache.
    page.goto(origin + path)
    check_favicon()
    page.reload()
    check_favicon()
    page.get_by_role("link", name="Dashboard", exact=True).click()
    expect(page).to_have_title("REPEATER SCRIBE")
    page.get_by_role("link", name="Callsigns", exact=True).click()
    if path != "/callsigns":
        page.locator("#callsign-query").fill("KM7GHS")
        page.locator("#callsign-search button").click()
    expect(page).to_have_url(origin + path)
    check_favicon()
