/** The choices for whose something is: the people who have signed in, plus whoever it's already set to (an old name
 * isn't lost), in alphabetical order, and "Joint" last where that makes sense (an account, not a card). */
export function ownerChoices(owners: string[], current: string | null | undefined, joint = false): string[] {
  const names = owners.filter((n) => n !== "Joint");
  if (current && current !== "Joint" && !names.includes(current)) names.push(current);
  names.sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base" }));
  return joint ? [...names, "Joint"] : names;
}
