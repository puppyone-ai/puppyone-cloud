export const sharedUiMarkdownFixture = `# PuppyOne

Shared editor fixture for Cloud and Desktop visual checks.
`;

export const sharedUiJsonFixture = JSON.stringify(
  {
    workspace: "PuppyOne",
    sharedUi: true,
    modes: ["cloud", "desktop"],
  },
  null,
  2,
);

