// Retire the compiled map's cache-first worker when moving to source evidence.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", event => {
  event.waitUntil((async () => {
    const scope = self.registration.scope;
    await self.registration.unregister();
    const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    await Promise.all(windows
      .filter(client => client.url.startsWith(scope))
      .map(client => client.navigate(client.url)));
  })());
});
