## ADDED Requirements

### Requirement: Inbound events are stored before they are acknowledged
The system SHALL verify the platform's signature, store the event durably,
and only then acknowledge it. It SHALL NOT start agent work inside the
webhook request.

#### Scenario: A verified message arrives
- **WHEN** a platform delivers a message with a valid signature
- **THEN** one inbox row exists for it and the platform receives a success
  response within 3 seconds

#### Scenario: The signature is wrong
- **WHEN** a request fails signature verification
- **THEN** it is rejected and no inbox row is created

### Requirement: A redelivered event never starts a second Run
The system SHALL treat events with the same platform event id as one event,
and SHALL derive the Run request id from the platform message id.

#### Scenario: The platform retries a delivery
- **WHEN** the same event is delivered twice
- **THEN** exactly one inbox row and exactly one Run exist

#### Scenario: The gateway restarts after submitting
- **WHEN** a gateway instance stops after submitting a Run but before
  recording it, and another instance processes the same inbox row
- **THEN** the same Run is returned and no second Run is created

### Requirement: Only messages addressed to the agent are kept
The system SHALL store a group message only when it mentions the agent,
replies to the agent, or is a command addressed to the agent. This is not
a setting.

#### Scenario: The platform delivers an ordinary group message
- **WHEN** a group message that is not addressed to the agent arrives
- **THEN** no inbox row is written and its text is not logged

### Requirement: Only an allowed chat can start a Run
The system SHALL start Runs only for a chat that a project manager has
allowed in PuppyOne and that is currently enabled.

#### Scenario: A chat has knocked but was not allowed
- **WHEN** someone mentions the agent in a chat whose state is asking
- **THEN** no Run is created and nothing is posted

#### Scenario: The link code is used by someone else
- **WHEN** an unknown chat presents a valid link code
- **THEN** the manager sees that chat's name and no access exists until
  the manager allows it

### Requirement: The asker is checked against the chat's setting
The system SHALL apply "who can ask" (owner, project, anyone) to every
message before submitting a Run.

#### Scenario: Only the owner may ask
- **WHEN** the setting is owner and another person asks
- **THEN** no Run is created and the person is told who can ask

#### Scenario: Project people may ask
- **WHEN** the setting is project and the asker's linked PuppyOne account
  lacks AGENT_RUN on the project
- **THEN** no Run is created

### Requirement: Runs execute as the responsible person
The system SHALL submit every chat Run as the chat's owner through
AgentService.submit, which re-checks the owner's AGENT_RUN.

#### Scenario: The owner lost access
- **WHEN** the owner no longer has AGENT_RUN on the project
- **THEN** no Run is created, the chat stops answering, and a project
  manager can take over

### Requirement: Approvals happen in PuppyOne, not in the chat
The system SHALL NOT offer approval buttons in a chat. When a Run waits
for approval, the chat message SHALL say whose approval it waits for, and
only the owner SHALL be able to decide it through PuppyOne's existing
approval interface.

#### Scenario: A Run waits for approval
- **WHEN** a chat Run enters waiting_approval
- **THEN** the chat message names the owner and the action, and carries
  no Allow or Deny control

#### Scenario: Nobody answers
- **WHEN** 30 minutes pass without a decision
- **THEN** the Run stops and the chat message says so

### Requirement: One chat message follows one Run
The system SHALL show a Run's progress by editing a single message, and
SHALL resume editing that same message after a gateway restart.

#### Scenario: The gateway restarts mid-Run
- **WHEN** a gateway instance stops while a Run is in progress
- **THEN** another instance continues editing the same message

### Requirement: Questions in one thread run in order
The system SHALL run at most one Run per thread at a time and SHALL
start waiting questions in the order they were received.

#### Scenario: A second question arrives while a Run is active
- **WHEN** a question arrives in a thread whose Run has not finished
- **THEN** it waits and starts only after that Run ends, before any later
  question in the same thread

### Requirement: Customer secrets are encrypted and never returned
The system SHALL store platform credentials encrypted at rest, SHALL NOT
return them from any route, and SHALL NOT write them to logs.

#### Scenario: A manager lists the chats of a project
- **WHEN** the chat list or any connection is returned to a client
- **THEN** it carries no secret and no ciphertext

### Requirement: The daily limit refuses new questions
The system SHALL refuse new questions in a chat once its usage for the
day reaches the chat's daily limit, without interrupting a Run that has
already started.

#### Scenario: The limit is reached
- **WHEN** a chat's usage today has reached its daily limit and someone asks
- **THEN** no Run is created and the chat is told the limit is used up

### Requirement: Untrusted senders never start a Run
The system SHALL drop messages from other bots and SHALL refuse questions
from Slack Connect external members and Slack guests, whatever the chat's
"who can ask" setting is.

#### Scenario: An external member asks in a shared channel
- **WHEN** a Slack Connect external member mentions the agent
- **THEN** no Run is created and that person is told who can ask

### Requirement: One platform message is one question
The system SHALL deduplicate inbound messages by the platform's message
identity, not by the delivery envelope.

#### Scenario: Telegram delivers a mention twice
- **WHEN** the same Telegram group message arrives as a message update and
  as a guest_message update
- **THEN** exactly one inbox row and at most one Run exist

#### Scenario: Slack edits a message
- **WHEN** Slack delivers a message_changed event
- **THEN** no inbox row is written

### Requirement: A WeCom chat knocks with the code
The system SHALL create a WeCom conversation only from a mention that
carries an unexpired, unused code shown in PuppyOne.

#### Scenario: A mention without the code
- **WHEN** someone mentions the bot in an unconnected WeCom group without
  the code
- **THEN** no conversation is created and nothing is posted

### Requirement: Management actions appear in project activity
The system SHALL record adding, allowing, denying, updating, taking over
and removing a chat as project activity, without message text.

#### Scenario: A chat is allowed
- **WHEN** a manager allows a chat
- **THEN** the project's activity list shows one chat_app item for it

### Requirement: Open in PuppyOne reveals nothing to outsiders
The system SHALL open the linked project session only for a signed-in
person who can read the project.

#### Scenario: Someone without access opens the link
- **WHEN** a signed-in person without AGENT_READ opens the link
- **THEN** they see that they have no access and no project content
