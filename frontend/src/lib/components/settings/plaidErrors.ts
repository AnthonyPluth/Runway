// What a Plaid connection's stored error means (PlaidItem.error: Plaid's error code, or the message when there was no
// code, such as Plaid not answering), in words, and whether Reconnect fixes it. Reconnecting opens Plaid Link in update
// mode, which only helps when the bank wants you to sign in or consent again; for the rest (the bank is down, too many
// requests), Sync is still the way, and the next sync usually clears it.

/** Fixed by signing in again through Plaid Link's update mode. */
const RECONNECT: Record<string, string> = {
  ITEM_LOGIN_REQUIRED: "Your bank needs you to sign in again",
  PENDING_EXPIRATION: "Your bank’s permission expires soon, reconnect to keep syncing",
  PENDING_DISCONNECT: "Your bank will disconnect Runway soon, reconnect to keep syncing",
  INVALID_CREDENTIALS: "Your bank didn’t accept the saved sign-in, reconnect to enter it again",
  INVALID_MFA: "Your bank didn’t accept the verification code, reconnect to try again",
  ITEM_LOCKED: "Your bank locked the login after too many tries; unlock it at the bank, then reconnect",
  ACCESS_NOT_GRANTED: "Your bank wants you to share these accounts again, reconnect to choose them",
  ADDITIONAL_CONSENT_REQUIRED: "Your bank wants your permission for more data, reconnect to give it",
};

/** Not fixed by reconnecting: they pass, or aren't something you can change. */
const OTHER: Record<string, string> = {
  INSTITUTION_DOWN: "The bank isn’t answering right now; Runway will try again",
  INSTITUTION_NOT_RESPONDING: "The bank isn’t answering right now; Runway will try again",
  INSTITUTION_NOT_AVAILABLE: "The bank isn’t available through Plaid right now; Runway will try again",
  INTERNAL_SERVER_ERROR: "Plaid had a problem; Runway will try again",
  RATE_LIMIT_EXCEEDED: "Too many requests to Plaid for now; Runway will try again later",
  PRODUCTS_NOT_SUPPORTED: "This bank doesn’t offer this through Plaid",
  NO_ACCOUNTS: "Plaid found no accounts at this bank that it can read",
};

interface PlaidProblem { text: string; reconnect: boolean; detail?: string }

/** A connection's error as a sentence, and whether to offer Reconnect. One Runway has no words for says it couldn't
 *  sync, with what Plaid said as `detail` (shown under Details). */
export function plaidProblem(error: string): PlaidProblem {
  if (Object.hasOwn(RECONNECT, error)) return { text: RECONNECT[error], reconnect: true };
  if (Object.hasOwn(OTHER, error)) return { text: OTHER[error], reconnect: false };
  return { text: "Couldn’t sync; Runway will try again", reconnect: false, detail: error };
}
