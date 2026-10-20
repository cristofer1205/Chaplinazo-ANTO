import os
import shutil
import threading

import pytest
from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server


@pytest.fixture
def browser_page(application):
    server = make_server("127.0.0.1", 0, application, threaded=True)
    url = f"http://127.0.0.1:{server.server_port}"
    application.config["ALLOWED_ORIGINS"] = [url]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            executable = os.environ.get("CHROMIUM_PATH") or shutil.which("chromium")
            browser = playwright.chromium.launch(executable_path=executable, headless=True)
            context = browser.new_context(viewport=dict(width=390, height=844))
            page = context.new_page()
            page.goto(url)
            yield page, context, url
            browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def enter_name(page, name="Persona A"):
    expect(page.locator("#name-dialog")).to_be_visible()
    page.locator("#actor").fill(name)
    page.get_by_role("button", name="Continuar al plano").click()


def test_required_name_gate_session_and_table_counter(browser_page):
    page, context, url = browser_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    assert page.locator("html").get_attribute("lang") == "es"
    expect(page.locator("#name-dialog")).to_be_visible()
    assert "sessionStorage" not in page.evaluate("Object.keys(localStorage).join(',')")
    assert page.locator("input").count() == 1
    assert page.locator("input[type=password], input[type=email], input[name*=buyer]").count() == 0
    assert page.locator("#map-tables a").count() == 69
    assert page.locator("#list-title, #tables").count() == 0
    expect(page.locator(".map-legend")).to_be_visible()
    expect(page.locator('#map-tables a[aria-label^="N.1,"] text').first).to_have_css("fill", "rgb(200, 50, 44)")
    assert page.locator("#map-tables a.empty").count() == 69
    expect(page.locator('#map-tables a[href="#map"]').first).to_have_attribute(
        "aria-label", "N.1, Platea, Vacía, 0 de 4 sillas ocupadas, 4 disponibles")
    assert not page.get_by_text("Acceso público: cualquier persona puede registrar ventas o corregir cualquier mesa.", exact=False).count()
    assert not page.get_by_text("Datos actualizados. Los nombres no están verificados.", exact=True).count()
    page.locator("#actor").fill("   ")
    page.get_by_role("button", name="Continuar al plano").click()
    expect(page.locator("#name-dialog")).to_be_visible()
    enter_name(page)
    expect(page.locator("#name-dialog")).not_to_be_visible()
    page.locator('#map-tables a[aria-label^="N.1,"]').dispatch_event("click")
    expect(page.locator("#table-dialog")).to_be_visible()
    expect(page.locator("#table-count")).to_have_text("0/4")
    expect(page.locator("#close-table")).to_have_text("OK")
    expect(page.get_by_text("Los cambios se guardan al instante. Puede cerrar con OK después de ver la confirmación.")).to_be_visible()
    progress = page.get_by_role("progressbar", name="Lugares registrados de 300")
    expect(progress).to_have_attribute("aria-valuemax", "300")
    expect(progress).to_have_attribute("aria-valuenow", "0")
    expect(progress).to_have_attribute("aria-valuetext", "0 de 300 lugares registrados")
    expect(page.locator("#totals .metric").nth(2)).to_contain_text("Mesas completas0")
    page.locator("#plus").click()
    expect(page.locator("#table-count")).to_have_text("1/4")
    expect(progress).to_have_attribute("aria-valuenow", "1")
    expect(page.locator("#totals .metric").nth(0)).to_contain_text("Entradas registradas1")
    expect(page.locator('#map-tables a[aria-label^="N.1,"]')).to_have_class("map-table partial")
    expect(page.locator('#map-tables a[aria-label^="N.1,"]')).to_have_attribute("aria-label", "N.1, Platea, Parcialmente ocupada, 1 de 4 sillas ocupadas, 3 disponibles")
    expect(page.locator("#message")).to_contain_text("Cambio guardado")
    page.locator("#close-table").click()
    expect(page.locator("#table-dialog")).not_to_be_visible()
    page.locator('#map-tables a[aria-label^="N.1,"]').dispatch_event("click")
    expect(page.locator("#table-count")).to_have_text("1/4")
    expect(page.locator("#history")).to_have_count(0)
    page.locator("#close-table").click()
    page.get_by_role("link", name="Historial de cambios").click()
    expect(page.locator("#history")).to_contain_text("Persona A")
    expect(page.locator("#history")).to_contain_text("Incremento de silla desde el plano")
    expect(page.locator("#history li").first).to_contain_text("Z")
    page.get_by_role("link", name="Plano").click()
    page.locator('#map-tables a[aria-label^="N.1,"]').dispatch_event("click")
    for count in (2, 3, 4):
        page.locator("#plus").click()
        expect(page.locator("#table-count")).to_have_text(f"{count}/4")
    expect(page.locator("#plus")).to_be_disabled()
    expect(page.locator('#map-tables a[aria-label^="N.1,"]')).to_have_class("map-table full")
    expect(page.locator('#map-tables a[aria-label^="N.1,"]')).to_contain_text("4/4")
    expect(progress).to_have_attribute("aria-valuenow", "4")
    expect(page.locator("#totals .metric").nth(2)).to_contain_text("Mesas completas1")
    page.reload()
    expect(page.locator("#name-dialog")).not_to_be_visible()
    page.locator('#map-tables a[aria-label^="N.1,"]').dispatch_event("click")
    expect(page.locator("#table-count")).to_have_text("4/4")
    for count in (3, 2, 1, 0):
        page.locator("#minus").click()
        expect(page.locator("#table-count")).to_have_text(f"{count}/4")
        expect(progress).to_have_attribute("aria-valuenow", str(count))
    expect(page.locator("#minus")).to_be_disabled()
    page.locator("#close-table").click()
    page.get_by_role("link", name="Historial de cambios").click()
    expect(page.locator("#history")).to_contain_text("Liberación de silla desde el plano")
    other = context.new_page(); other.goto(url)
    expect(other.locator("#name-dialog")).to_be_visible()
    assert not errors


def test_venue_backdrop_clip_preserves_interactive_map(browser_page):
    page, _context, _url = browser_page
    image = page.locator("#map image.venue-image")
    assert image.get_attribute("clip-path") == "url(#venue-backdrop-clip)"
    clip_paths = page.locator("#venue-backdrop-clip path")
    assert clip_paths.count() == 2
    assert "1150 687 L1090 687" in clip_paths.nth(0).get_attribute("d")
    assert "M1090 687 H1142 V827 H1090 Z" in clip_paths.nth(1).get_attribute("d")
    assert page.locator("#map-tables a").count() == 69
    assert page.locator("#map .outline").count() == 1
    assert page.locator("#map .stage").count() == 1
    enter_name(page)
    page.screenshot(path="/tmp/antonella-venue-map-clip.png", full_page=True)


def test_conflict_preserves_draft_offline_and_polling(browser_page, client, payload):
    page, context, _url = browser_page
    enter_name(page)
    page.locator('#map-tables a[aria-label^="N.1,"]').dispatch_event("click")
    assert client.post("/api/tables/1/sales", json=payload).status_code == 201
    page.locator("#plus").click()
    expect(page.locator("#message")).to_contain_text("Otro vendedor")
    expect(page.locator("#table-count")).to_have_text("1/4")
    expect(page.locator("#plus")).to_be_enabled()
    context.set_offline(True)
    expect(page.locator("#plus")).to_be_disabled()
    context.set_offline(False)
    expect(page.locator("#plus")).to_be_enabled(timeout=10000)
    assert client.post("/api/tables/2/sales", json=payload).status_code == 201
    expect(page.locator('#map-tables a[aria-label^="N.2,"]')).to_contain_text("1/4", timeout=10000)


def test_background_poll_adopts_changed_table_and_notifies(browser_page, client, payload):
    page, _context, _url = browser_page
    enter_name(page)
    page.locator('#map-tables a[aria-label^="N.1,"]').dispatch_event("click")
    assert client.post("/api/tables/1/sales", json=payload).status_code == 201
    expect(page.locator("#table-count")).to_have_text("1/4", timeout=10000)
    expect(page.locator("#message")).to_contain_text("Otro vendedor")
    expect(page.locator("#plus")).to_be_enabled()


def test_table_popup_has_no_manual_refresh_button(browser_page):
    page, _context, _url = browser_page
    assert page.locator("#table-refresh").count() == 0


def test_lost_response_never_replays(browser_page):
    page, _context, _url = browser_page
    attempts = []
    def abort(route):
        attempts.append(route.request.method)
        route.abort()
    page.route("**/api/tables/1/sales", abort)
    enter_name(page)
    page.locator('#map-tables a[aria-label^="N.1,"]').dispatch_event("click")
    page.locator("#plus").click()
    expect(page.locator("#message")).to_contain_text("Respuesta no confirmada")
    expect(page.locator("#table-count")).to_have_text("0/4")
    expect(page.locator("#plus")).to_be_enabled()
    page.wait_for_timeout(5500)
    assert attempts == ["POST"]
