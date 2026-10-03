// What's missing or wrong in the Add forms' fields before anything goes to the server, in the words each field's note
// uses (as Recurring's `validate` does). The server still checks everything; this is so you're told which field, and
// which section of the form it's in, without a round trip.
import { parseDate } from "$lib/format";

export type FormErrors = Record<string, string>;
type Values = Record<string, unknown>;

const blank = (x: unknown) => x == null || String(x).trim() === "";
const day = (x: unknown) => /^\d{4}-\d{2}-\d{2}$/.test(String(x)) && !Number.isNaN(parseDate(String(x)).getTime());
const below0 = (x: unknown) => !blank(x) && !(Number(x) >= 0);

/** Amounts that can be left empty but can't be below 0 or not a number. */
function amounts(v: Values, fields: [string, string][], e: FormErrors) {
  for (const [key, what] of fields) if (below0(v[key])) e[key] = `${what} can’t be below 0.`;
}

export function validateCard(v: Values): FormErrors {
  const e: FormErrors = {};
  if (blank(v.owner)) e.owner = "Choose whose card it is.";
  if (blank(v.issuer)) e.issuer = "Pick the bank.";
  if (blank(v.product)) e.product = "Enter the card’s name, like Sapphire Preferred.";
  if (!day(v.opened_on)) e.opened_on = "Enter the day it was opened.";
  amounts(v, [["annual_fee", "The annual fee"], ["bonus", "The bonus"], ["bonus_spend", "The spend"], ["manual_spend", "Spent so far"]], e);
  return e;
}

export function validateBank(v: Values): FormErrors {
  const e: FormErrors = {};
  if (blank(v.owner)) e.owner = "Choose whose account it is.";
  if (blank(v.bank)) e.bank = "Enter the bank, like Chase.";
  if (!day(v.opened_on)) e.opened_on = "Enter the day it was opened.";
  if (blank(v.bonus)) e.bonus = "Enter the bonus.";
  else if (!(Number(v.bonus) > 0)) e.bonus = "Enter a bonus above $0.";
  amounts(v, [["dd_total", "Direct deposits"], ["min_balance", "The balance"], ["monthly_fee", "The monthly fee"], ["early_close_fee", "The early-closing fee"]], e);
  return e;
}

export function validateWish(v: Values): FormErrors {
  const e: FormErrors = {};
  if (blank(v.owner)) e.owner = "Choose whose it is.";
  if (v.kind === "card") {
    if (blank(v.issuer)) e.issuer = "Pick the bank.";
    if (blank(v.product)) e.product = "Enter the card’s name, like Sapphire Preferred.";
  } else if (blank(v.bank)) e.bank = "Enter the bank, like Chase.";
  amounts(v, [["annual_fee", "The annual fee"], ["bonus", "The bonus"], ["bonus_spend", "The spend"]], e);
  return e;
}

export function validateBenefit(v: Values): FormErrors {
  const e: FormErrors = {};
  if (blank(v.name)) e.name = "Enter the benefit’s name, like Lyft credit.";
  if (v.kind === "credit") amounts(v, [["amount", "The amount"]], e);
  return e;
}
