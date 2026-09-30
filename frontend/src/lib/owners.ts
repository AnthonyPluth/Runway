/** The choices for whose something is: the people who have signed in, plus whoever it's already set to (an old name
 * isn't lost), and "Joint" where that makes sense (an account, not a card). */
export function ownerChoices(owners: string[], current: string | null | undefined, joint = false): string[] {
  const names = owners.filter((n) => n !== "Joint");
  if (current && current !== "Joint" && !names.includes(current)) names.push(current);
  return joint ? [...names, "Joint"] : names;
}
