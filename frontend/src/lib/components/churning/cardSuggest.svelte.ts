// "Fill in the rest" on the card form (when an OpenRouter key is set): what the AI knows of the card from its bank and
// name alone. It only fills what's still empty, and everything stays in the form, marked, until you save: adding saves
// it with the card; editing asks first (Save these / Discard).
import { act } from "$lib/act";
import { api } from "$lib/api";
import { toast } from "svelte-sonner";
import type { RateRow } from "./churning";
import type { CardSuggestion, ChurnCard, DraftBenefit } from "./types";

/** The card form's values the suggestion reads and fills (the form holds more). */
type Values = {
  issuer: string; product: string; family: string; currency: string; annual_fee: string; bonus: string; bonus_spend: string;
  bonus_months: string; base_rate: string;
} & Record<string, unknown>;

/** What the suggestions fill in on the form, which they need to reach into: the card (null when adding), its values and
 * earning rates, and the sections to open so what was filled can be checked. */
type Host = {
  card: ChurnCard | null;
  v: Values;
  rates: { rows: RateRow[]; portalName: string };
  open: { more: boolean; rates: boolean; benefits: boolean; bonus: boolean };
  ratesBody: () => object;
  /** A save went through, so closing the form redraws the page. */
  saved: () => void;
  onchanged: () => void | Promise<void>;
};

/** A source page's host name, for its link ("https://www.example.com/cards" → "example.com"). */
export const sourceHost = (u: string) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return u; } };

const benefitBody = ({ ai: _ai, ...b }: DraftBenefit) => ({ ...b, amount: b.amount === null || String(b.amount) === "" ? null : b.amount });

export class CardSuggest {
  suggesting = $state(false);
  /** Something the AI filled is in the form, waiting to be saved or discarded. */
  marked = $state(false);
  /** The benefits it suggested, shown (and editable) in the Benefits section. */
  benefits = $state<DraftBenefit[]>([]);
  /** Where the suggestions came from (web pages, http(s) only), and whether the AI searched the web at all. */
  sources = $state<string[]>([]);
  web = $state(false);
  #undo: (() => void) | null = null;
  #fields: Record<string, unknown> = {};
  #rates = false;

  constructor(private host: Host) {}

  /** The suggested benefits that have a name, as the server takes them. */
  benefitsBody = () => this.benefits.filter((b) => b.name.trim()).map(benefitBody);
  saveBenefits = async (cardId: number) => {
    for (const b of this.benefitsBody()) await api(`/api/churning/cards/${cardId}/benefits`, { method: "POST", body: b });
  };

  suggest = async () => {
    const { v } = this.host;
    await act(async () => {
      this.apply(await api<CardSuggestion>("/api/churning/suggest", { method: "POST", body: { issuer: v.issuer, product: v.product } }));
    }, { busy: (on) => (this.suggesting = on) });
  };

  apply(s: CardSuggestion) {
    const { card: c, v, rates: r, open } = this.host;
    const before: Record<string, unknown> = { ...v }, rows = r.rows.map((x) => ({ ...x })), portal = r.portalName, benefits = this.benefits;
    const fields: Record<string, unknown> = {};
    if (s.family && !v.family.trim()) { v.family = s.family; fields.family = s.family; }
    if (s.currency && v.currency === "cash" && s.currency !== "cash") { v.currency = s.currency; fields.currency = s.currency; }
    if (s.annual_fee && !v.annual_fee) { v.annual_fee = String(s.annual_fee); fields.annual_fee = s.annual_fee; }
    if (s.portal_name && !r.portalName.trim()) { r.portalName = s.portal_name; fields.portal_name = s.portal_name; }
    // The public sign-up offer, only for a card you're adding (one you have came with the offer you applied with).
    if (!c && s.bonus?.amount && !v.bonus && !v.bonus_spend) {
      v.bonus = String(s.bonus.amount); fields.bonus = v.bonus;
      if (s.bonus.spend) { v.bonus_spend = String(s.bonus.spend); fields.bonus_spend = v.bonus_spend; }
      if (s.bonus.months && ["", "3"].includes(String(v.bonus_months))) { v.bonus_months = String(s.bonus.months); fields.bonus_months = v.bonus_months; }
    }
    let rates = false;
    if (!r.rows.length && (s.rates.length || s.base_rate != null)) {
      if (s.base_rate != null && ["", "1"].includes(String(v.base_rate))) { v.base_rate = String(s.base_rate); fields.base_rate = v.base_rate; }
      r.rows = s.rates.map((x) => ({ category: x.category, multiplier: x.multiplier, portal_only: !!x.portal_only }));
      rates = true;
    }
    const have = new Set([...(c?.benefits ?? []), ...this.benefits].map((b) => b.name.toLowerCase()));   // the card's, and ones already suggested
    const fresh = s.benefits.filter((b) => !have.has(b.name.toLowerCase()));
    if (!Object.keys(fields).length && !rates && !fresh.length) { toast("The AI had nothing to add for this card"); return; }
    this.benefits = [...this.benefits, ...fresh.map((b) => ({ ...b, ai: true }))];
    // Sections that got something open, so the suggestions can be checked.
    if (fields.family) open.more = true;
    if (fields.currency || fields.portal_name || rates) open.rates = true;
    if (fresh.length) open.benefits = true;
    if (fields.bonus) open.bonus = true;
    this.sources = [...new Set([...this.sources, ...(s.sources ?? []).filter((u) => /^https?:\/\//i.test(u))])];
    this.web = !!s.web;
    this.#fields = { ...this.#fields, ...fields };
    this.#rates = this.#rates || rates;
    const prev = this.#undo;
    // Discard takes back only what the AI filled, and only where it still holds the AI's value: anything you changed
    // since (the opened date, the bonus) stays as you left it.
    const filledRows = JSON.stringify(r.rows);
    this.#undo = () => {
      for (const [k, val] of Object.entries(fields)) {
        if (k === "portal_name") { if (r.portalName === val) r.portalName = portal; }
        else if (String(v[k]) === String(val)) v[k] = before[k];
      }
      if (rates && JSON.stringify(r.rows) === filledRows) r.rows = rows;
      this.benefits = benefits;
      prev?.();
    };
    this.marked = true;
  }

  private clear() { this.#undo = null; this.#fields = {}; this.#rates = false; this.marked = false; this.sources = []; }

  discard = () => { this.#undo?.(); this.clear(); };

  /** Editing a card: keep what the AI filled (and its benefits). */
  save = async () => {
    const { card: c, v, rates: r } = this.host;
    await act(async () => {
      // What the form holds now for the fields the AI filled: you may have corrected one since.
      const now = Object.fromEntries(Object.keys(this.#fields).map((k) => [k, k === "portal_name" ? r.portalName : v[k]]));
      await api(`/api/churning/cards/${c!.id}`, { method: "POST", body: { ...now, ...(this.#rates ? this.host.ratesBody() : {}) } });
      await this.saveBenefits(c!.id);
      this.benefits = []; this.clear(); this.host.saved();
      toast("Saved the suggestions");
      await this.host.onchanged();
    });
  };
}
