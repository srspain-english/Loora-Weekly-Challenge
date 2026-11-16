import { test, expect } from "@playwright/test";
test.beforeEach(async ({ request }) => {
  const r = await request.post("/api/preview/reset", {
    headers: { Origin: "http://127.0.0.1:5400" },
  });
  expect(r.ok()).toBeTruthy();
});
async function start(page: any, mode = "free") {
  await page.goto("/");
  await page.getByTestId(`button-start-${mode}`).click();
  await page.getByTestId("button-begin-conversation").click();
  await expect(page.getByTestId("status-session")).toHaveText(
    "Your turn to speak or type",
  );
}
test("branding, honest empty progress and responsive navigation", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.getByRole("note")).toContainText("Fictional preview");
  await expect(page.getByTestId("button-start-free")).toContainText(
    "gentle correction rules",
  );
  await expect(page.getByTestId("button-open-notes")).toContainText(
    "Finish a class",
  );
  await expect(page.getByText("Fluent.")).toHaveCount(0);
  await page.getByRole("link", { name: "Progress", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Building the picture" }),
  ).toBeVisible();
  await expect(page.getByText("0 finished classes")).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
});
test("streamed conversation, help, pause and canonical report", async ({
  page,
}) => {
  await start(page);
  await expect(page.getByTestId("teacher-avatar-slot")).toBeVisible();
  await page.getByTestId("button-help").click();
  await expect(page.getByTestId("status-help")).toContainText("Juno te pide");
  await page
    .getByLabel("Your answer")
    .fill("I am working here since two weeks.");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByTestId("text-session-caption")).toContainText(
    "I’ve been working here for two weeks.",
  );
  await expect(page.getByTestId("status-session")).toHaveText(
    "Your turn to speak or type",
  );
  await page.getByTestId("button-toggle-pause").click();
  await expect(page.getByTestId("status-session")).toHaveText("Paused");
  await expect(page.getByLabel("Your answer")).toBeDisabled();
  await page.getByTestId("button-toggle-pause").click();
  await page.getByTestId("button-end-session").click();
  await expect(page.getByTestId("card-correction-0")).toContainText(
    "I’ve been working here for two weeks.",
  );
  await expect(
    page.getByText("Pronunciation practice · no scores"),
  ).toBeVisible();
  await expect(page.getByText("64%")).toHaveCount(0);
  await page.getByRole("link", { name: "Progress", exact: true }).click();
  await expect(page.getByText("1 finished classes")).toBeVisible();
  await expect(page.getByText("1 exchanges")).toBeVisible();
});
test("zero corrections and vocabulary from saved reports", async ({ page }) => {
  await start(page);
  await page.getByLabel("Your answer").fill("I would like a little more time.");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByTestId("status-session")).toHaveText(
    "Your turn to speak or type",
  );
  await page.getByTestId("button-end-session").click();
  await expect(
    page.getByText("No corrections recorded.", { exact: false }),
  ).toBeVisible();
  await page.goto("/drill");
  await expect(
    page.getByText("a little more time", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("not a listening assessment", { exact: false }),
  ).toBeVisible();
});
test("all existing class modes and real Juno lesson catalogue", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByTestId("button-start-business").click();
  expect(
    await page.getByLabel("Juno lesson").locator("option").count(),
  ).toBeGreaterThan(10);
  await expect(page.getByLabel("Juno lesson")).toContainText(
    "Always On the Ball",
  );
  await page.getByTestId("button-begin-conversation").click();
  await expect(page.getByText("Juno · business scenario")).toBeVisible();
  await expect(page.getByTestId("status-session")).toHaveText(
    "Your turn to speak or type",
  );
  await page.getByTestId("button-end-session").click();
  await expect(page.getByText("Your class,")).toBeVisible();
  await page.goto("/");
  await page.getByTestId("button-start-structured").click();
  await page.getByTestId("button-begin-conversation").click();
  await expect(page.getByText("Juno · structured class")).toBeVisible();
});
test("saved class resumes without duplicate opening", async ({ page }) => {
  let starts = 0;
  page.on("request", (r) => {
    if (r.url().endsWith("/api/start")) starts++;
  });
  await start(page);
  await page.getByRole("link", { name: "Leave class saved" }).click();
  await page.getByTestId("button-start-free").click();
  await expect(page.getByTestId("button-begin-conversation")).toContainText(
    "Resume saved class",
  );
  await page.getByTestId("button-begin-conversation").click();
  await expect(page.getByTestId("status-session")).toHaveText(
    "Your turn to speak or type",
  );
  expect(starts).toBe(1);
});
test("errors are visible and typed input survives", async ({ page }) => {
  await start(page);
  await page.route("**/api/message", (route) =>
    route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ error: "Preview temporarily unavailable" }),
    }),
  );
  await page.getByLabel("Your answer").fill("Fictional retry");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText(
    "Preview temporarily unavailable",
  );
  await expect(page.getByLabel("Your answer")).toHaveValue("Fictional retry");
});
test("keyboard focus, reduced motion and 320px layout", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 740 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/");
  await expect(page.getByTestId("button-start-free")).toBeVisible();
  await page.keyboard.press("Tab");
  expect(await page.evaluate(() => document.activeElement?.tagName)).not.toBe(
    "BODY",
  );
  await page.getByTestId("button-start-free").click();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  await page.getByTestId("button-begin-conversation").click();
  await expect(page.getByTestId("status-session")).toHaveText(
    "Your turn to speak or type",
  );
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
});
test("assets and API traffic stay on loopback", async ({ page }) => {
  const external: string[] = [];
  page.on("request", (r) => {
    if (!r.url().startsWith("http://127.0.0.1:5400")) external.push(r.url());
  });
  await page.goto("/");
  await expect(page.getByTestId("button-start-free")).toBeVisible();
  await page.goto("/notes");
  await expect(
    page.getByRole("heading", {
      name: "Your next page starts with a conversation.",
    }),
  ).toBeVisible();
  expect(external).toEqual([]);
});
