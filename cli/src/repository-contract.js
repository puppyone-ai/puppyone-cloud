export const REPOSITORY_CONTRACT_VERSION = "2";

export function repositoryContractHeaders() {
  return {
    "X-PuppyOne-Repository-Contract": REPOSITORY_CONTRACT_VERSION,
  };
}
