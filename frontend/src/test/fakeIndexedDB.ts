// A small in-memory IndexedDB for tests: what lib/idb.ts uses of it (open with an upgrade, one object store with a
// key path, get, getAll, put, delete, transactions that complete, close, versionchange, deleteDatabase), answering
// asynchronously on microtasks (so it works under fake timers), with switches to make it fail as browsers do: a
// private window or blocked storage (open fails), a full disk (writes fail), or IndexedDB not there at all.
type Value = Record<string, unknown>;
type Req = { result: unknown; error: unknown; onsuccess: (() => void) | null; onerror: (() => void) | null;
  onupgradeneeded?: (() => void) | null; onblocked?: (() => void) | null };

const later = (fn: () => void) => { void Promise.resolve().then(fn); };

export class FakeIndexedDB {
  /** Databases by name: store name → (key → value). What a test reads to see what's on the "disk". */
  dbs = new Map<string, Map<string, Map<string, Value>>>();
  failOpen = false;
  failWrites = false;
  opened = 0;
  private conns = new Set<FakeDB>();

  open(name: string, _version?: number): Req {
    const req: Req = { result: null, error: null, onsuccess: null, onerror: null, onupgradeneeded: null, onblocked: null };
    later(() => {
      if (this.failOpen) { req.error = new DOMException("The operation is insecure.", "SecurityError"); req.onerror?.(); return; }
      this.opened++;
      const fresh = !this.dbs.has(name);
      if (fresh) this.dbs.set(name, new Map());
      const db = new FakeDB(this, name);
      this.conns.add(db);
      req.result = db;
      if (fresh) req.onupgradeneeded?.();
      req.onsuccess?.();
    });
    return req;
  }

  deleteDatabase(name: string): Req {
    const req: Req = { result: undefined, error: null, onsuccess: null, onerror: null, onblocked: null };
    later(() => {
      for (const c of [...this.conns]) if (c.name === name && !c.closed) c.onversionchange?.();
      if ([...this.conns].some((c) => c.name === name && !c.closed)) { req.onblocked?.(); return; }
      this.dbs.delete(name);
      req.onsuccess?.();
    });
    return req;
  }

  /** Every row of a database's store, as stored. */
  rows(db = "runway-cache", store = "rows"): Value[] {
    return [...(this.dbs.get(db)?.get(store)?.values() ?? [])];
  }

  forget(c: FakeDB) { this.conns.delete(c); }
}

class FakeDB {
  closed = false;
  onversionchange: (() => void) | null = null;
  onclose: (() => void) | null = null;
  keyPaths = new Map<string, string>();
  constructor(readonly idb: FakeIndexedDB, readonly name: string) {}
  private get stores() { return this.idb.dbs.get(this.name)!; }
  get objectStoreNames() { return { contains: (s: string) => this.stores.has(s) }; }
  createObjectStore(s: string, opts: { keyPath: string }) { this.stores.set(s, new Map()); this.keyPaths.set(s, opts.keyPath); }
  close() { this.closed = true; this.idb.forget(this); }
  transaction(s: string, mode: string) {
    if (this.closed) throw new DOMException("The database connection is closing.", "InvalidStateError");
    if (!this.idb.dbs.has(this.name)) throw new DOMException("gone", "InvalidStateError");
    return new FakeTx(this, s, mode);
  }
  store(s: string) { return this.stores.get(s)!; }
}

class FakeTx {
  oncomplete: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;
  error: unknown = null;
  private pending = 0;
  private failed = false;
  constructor(private db: FakeDB, private name: string, private mode: string) {
    later(() => later(() => this.finish()));
  }
  private finish() {
    if (this.pending) { later(() => this.finish()); return; }
    if (this.failed) this.onabort?.(); else this.oncomplete?.();
  }
  objectStore(_s: string) {
    const data = this.db.store(this.name), keyPath = "k";
    const request = (work: () => unknown, write = false): Req => {
      const req: Req = { result: undefined, error: null, onsuccess: null, onerror: null };
      this.pending++;
      later(() => {
        this.pending--;
        if (write && this.db.idb.failWrites) {
          req.error = this.error = new DOMException("The quota has been exceeded.", "QuotaExceededError");
          this.failed = true;
          req.onerror?.(); this.onerror?.();
          return;
        }
        req.result = work();
        req.onsuccess?.();
      });
      return req;
    };
    const ro = () => { if (this.mode !== "readwrite") throw new DOMException("read only", "ReadOnlyError"); };
    return {
      get: (k: string) => request(() => (data.has(k) ? structuredClone(data.get(k)) : undefined)),
      getAll: () => request(() => [...data.values()].map((v) => structuredClone(v))),
      put: (v: Value) => { ro(); const copy = structuredClone(v); return request(() => { data.set(String(copy[keyPath]), copy); return copy[keyPath]; }, true); },
      delete: (k: string) => { ro(); return request(() => { data.delete(k); }, true); },
    };
  }
}

/** Put a fresh fake IndexedDB in place (globalThis.indexedDB) and return it. */
export function installFakeIndexedDB(): FakeIndexedDB {
  const f = new FakeIndexedDB();
  Object.defineProperty(globalThis, "indexedDB", { value: f, configurable: true, writable: true });
  return f;
}
/** IndexedDB not there at all (an old browser, or storage blocked). */
export function removeIndexedDB(): void {
  Object.defineProperty(globalThis, "indexedDB", { value: undefined, configurable: true, writable: true });
}
