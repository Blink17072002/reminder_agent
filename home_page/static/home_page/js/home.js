// Enable GitHub-flavored markdown and line breaks
marked.setOptions({
        gfm: true,
        breaks: true,
        smartLists: true,
        smartypants: false,
        headerIds: false,
        mangle: false
    });
    
    // Sidebar collapse toggle
document.querySelectorAll('.sidebar-toggle-btn').forEach(btn => {
        btn.addEventListener('click', () => {
                document.querySelector('.sidebar-wrapper').classList.toggle('collapsed');
                document.body.classList.toggle('sidebar-collapsed');
        });
});
    
// Autosize the textarea as the user types
const textarea = document.querySelector('.chat-textarea');
textarea.addEventListener('input', () => {
        textarea.style.height = 'auto';
        textarea.style.height = textarea.scrollHeight + 'px';
});
    
function scrollChatToBottom() {
        const chatMessages = document.querySelector('.chat-messages');
        // Use smooth scrolling to the last message
        const last = chatMessages.querySelector('.message:last-child');
        if (last) {
                last.scrollIntoView({ behavior: 'smooth', block: 'end' });
        } else {
                // Fallback to instant scroll if no messages yet
                chatMessages.scrollTop = chatMessages.scrollHeight;
        }
}
    
    
document.addEventListener("DOMContentLoaded", () => {
        const form = document.getElementById("chat-form");
        const log = document.getElementById("chat-log");
        const isNewConversationPageInitialLoad = document.body.hasAttribute('data-is-new-conversation'); // Flag from Django template on initial load
    
    
        // Get user initial and bot icon
        const userAvatarMain = document.querySelector('.nav-user .avatar');
        const userInitial = userAvatarMain ? userAvatarMain.textContent.trim() : '';
        const botIconSVG = `<svg width="20" height="20" viewBox="0 0 24 24" fill="none">
            <path d="M19 4h-1V2h-2v2H8V2H6v2H5c-1.1 0-2 .9-2 2v14
            c0 1.1.9 2 2 2h14
            c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zM19 20H5V8h14v12z"
            fill="#5F6368"/>
        </svg>`;
    
         // Function to type text character by character into a bubble element
         function typeText(element, text, speed = 5, callback = null) {
             let i = 0;
             const textStr = String(text); // Ensure text is string
             element.textContent = ""; // Clear content initially
             function nextChar() {
                 if (i < textStr.length) {
                     element.textContent = textStr.substring(0, i + 1);
                     i++;
                     setTimeout(nextChar, speed);
                 } else {
                     // Once typing is done, parse markdown
                     element.innerHTML = marked.parse(textStr);
                     scrollChatToBottom(); // *** ONLY SCROLL ONCE AFTER ANIMATION FINISHES ***
                     if (callback) {
                         callback(); // Execute callback after animation finishes
                     }
                 }
             }
             nextChar();
         }
    
    
        // Append a message to the chat log
        // This function is primarily for adding messages dynamically after initial load
        function appendMessage(who, text) {
            const messageEl = document.createElement("div");
            messageEl.classList.add("message", who === "user" ? "user-message" : "bot-message");
    
            const avatarEl = document.createElement("div");
            avatarEl.classList.add("avatar", who === "user" ? "user-avatar" : "bot-avatar");
            avatarEl.innerHTML = who === "user" ? userInitial : botIconSVG;
    
            const bubbleEl = document.createElement("div");
            bubbleEl.classList.add("bubble");
    
            if (who === "user") {
                // User messages are rendered immediately with markdown
                bubbleEl.innerHTML = marked.parse(String(text)); // Ensure text is string
            } else { // Agent message (from POST response)
                // Initial content is empty, will be filled by typeText
                bubbleEl.textContent = "";
            }
    
            messageEl.appendChild(avatarEl);
            messageEl.appendChild(bubbleEl);
            log.appendChild(messageEl);
            // scrollChatToBottom(); // AppendMessage itself doesn't force scroll anymore
    
            // If it's an agent message, return the bubble element to be used with typeText
            if (who === "agent") {
                 return bubbleEl;
            }
            return null; // Return null for user messages
        }
    
    
        function updateRecentsTitle(convoId, newTitle) {
            console.log(`Attempting to update title for convo ID: ${convoId} with title: "${newTitle}".`);
            const recentsList = document.getElementById('recents-list');
            if (recentsList) {
                // Use robust selector to find the link element for the conversation
                const convoLink = recentsList.querySelector(`li a[href$="/assistant/${convoId}/"]`);
                if (convoLink) {
                    console.log(`Link element found for convo ID ${convoId}.`);
                    const plainTitle = String(newTitle || "New Chat").trim(); // Ensure title is string, use fallback
    
                    // Check if title is already correct to avoid re-typing unnecessarily
                    if (convoLink.textContent.trim() === plainTitle) {
                         console.log("Title already matches, skipping animation.");
                          // Ensure active class is correct even if no typing
                         recentsList.querySelectorAll('li').forEach(li => li.classList.remove('active'));
                         convoLink.parentElement.classList.add('active');
                         return; // Exit if no update needed
                    }
    
                    console.log("Title needs update. Starting animation.");
                    convoLink.textContent = ""; // Clear the link text to start animation
                    let i = 0;
    
                    function typeTitleChar() {
                        if (i < plainTitle.length) {
                            convoLink.textContent += plainTitle.charAt(i++);
                            // Small delay between characters (adjust speed as needed)
                            setTimeout(typeTitleChar, 20); // Adjust speed here (milliseconds)
                        } else {
                            console.log(`Title animation complete for convo ID ${convoId}.`);
                            convoLink.textContent = plainTitle; // Ensure final text is set
                            // Also ensure the parent li is marked active after animation
                            recentsList.querySelectorAll('li').forEach(li => li.classList.remove('active'));
                            convoLink.parentElement.classList.add('active');
                        }
                    }
                    typeTitleChar(); // Start the animation
    
                } else {
                    console.log(`Could not find link for convo ID ${convoId} with selector 'li a[href$="/assistant/${convoId}/"]'.`);
                     // If the link wasn't found, it means the LI for this conversation
                     // hasn't been added to the sidebar yet dynamically (should happen for the first message POST).
                     // This case is handled in the form submit handler.
                }
            } else {
                console.log("Recents list element #recents-list not found when trying to update title.");
            }
        }
    
        // --- START: Initial Page Load Rendering & Animation ---
        // On initial page load, find the welcome message if it exists and animate it.
        // All other existing messages should be rendered by Django template with markdown parsed.
    
        // Find the welcome message bubble rendered by Django (if it's a new convo page based on the flag)
        const welcomeMessageBubble = log.querySelector('.bot-message .bubble[data-welcome-message="true"]');
    
        if (isNewConversationPageInitialLoad && welcomeMessageBubble) {
            console.log("Page loaded with welcome message. Starting animation.");
            const rawText = welcomeMessageBubble.getAttribute('data-raw');
            if (rawText) {
                // Animate the welcome message
                // typeText now handles the final scroll itself
                typeText(welcomeMessageBubble, rawText, 10); // Adjust speed here (e.g., 10ms per character)
            } else {
                console.warn("Welcome message bubble found on load, but no data-raw attribute. Rendering static markdown.");
                // Fallback: If raw data is missing, just render markdown
                if (welcomeMessageBubble.textContent) {
                    welcomeMessageBubble.innerHTML = marked.parse(welcomeMessageBubble.textContent);
                }
                 // Ensure scroll happens for the static fallback
                scrollChatToBottom();
            }
        } else {
            console.log("Page loaded without a welcome message. Rendering existing messages if any.");
            // If not a new conversation page (no welcome message rendered by Django),
            // render markdown for all agent messages that were loaded by Django.
            document.querySelectorAll('.bot-message .bubble[data-raw]').forEach(bubble => {
                const raw = bubble.getAttribute('data-raw');
                if (raw) {
                    bubble.innerHTML = marked.parse(raw);
                }
            });
             // Always scroll to bottom on page load after rendering existing messages
            scrollChatToBottom();
        }
        // --- END: Initial Page Load Rendering & Animation ---
    
    
        // Chat submit handler
        form.addEventListener("submit", async e => {
            e.preventDefault();
            const input = document.getElementById("chat-input");
            const msg = input.value.trim();
            if (!msg) {
                // If message is empty, append user bubble, clear input, and scroll, but don't POST
                // Decide if you want an empty user bubble or just clear input
                // appendMessage("user", ""); // Optional: Append empty bubble
                input.value = '';
                input.style.height = 'auto';
                // scrollChatToBottom(); // Handled if appendMessage is used
                return; // Stop here if message is empty
            }
    
            // Append user message bubble immediately
            appendMessage("user", msg);
            input.value = '';
            input.style.height = 'auto';
            // scrollChatToBottom() is called inside appendMessage
    
    
            // Create a temporary bubble element for the agent's reply (will be animated)
            const agentBubble = appendMessage("agent", ""); // Use appendMessage for agent, returns bubble element
    
    
            // Get csrf token just before fetching
            const csrfToken = form.querySelector("[name=csrfmiddlewaretoken]").value;
    
            // Determine the correct URL to POST to
            // Use form.action as the target URL. This is updated by JS after the first POST.
            const currentPath = form.action;
            // Use .slice() for JavaScript strings
            console.log(`Submitting form to: ${currentPath}. Message: "${msg.slice(0, 50)}...".`);
    
    
            // Fetch the AI reply (POST to current form action URL)
            try {
                const resp = await fetch(currentPath, {
                        method: "POST",
                    headers: {
                                "Content-Type": "application/x-www-form-urlencoded",
                                "X-CSRFToken": csrfToken
                        },
                        body: new URLSearchParams({ message: msg })
                });
    
                if (!resp.ok) {
                     console.error("Fetch failed with status:", resp.status, resp.statusText);
                     agentBubble.innerHTML = `<span style="color:red">Error communicating with the server (${resp.status}). Please try again.</span>`;
                     scrollChatToBottom(); // Ensure scroll after error message
                     return; // Stop processing if fetch failed
                }
    
                const data = await resp.json();
                console.log("AJAX response data received:", data);
    
                 // Check if the backend indicates this was the first actual message in the convo
                 const isFirstActualMessage = data.is_first_actual_message === true;
                 console.log(`Backend reports is_first_actual_message: ${isFirstActualMessage}`);
    
    
                // --- START: Handle new conversation creation & URL update (if it was the first message) ---
                 // This block only runs if the backend confirmed it was the first message
                 if (isFirstActualMessage && data.convo_id) {
                     console.log("Backend created a new conversation with this message. Updating URL and sidebar.");
                     const newConvoUrl = `/agent/assistant/${data.convo_id}/`;
    
                     // Update the browser URL without a full page reload
                     window.history.pushState({}, '', newConvoUrl);
    
                     // Update the form action to the new conversation URL for subsequent posts
                     form.action = newConvoUrl;
    
                     // Add the new conversation to the sidebar recents list if it doesn't exist
                     const recentsList = document.getElementById('recents-list');
                     if (recentsList) {
                         const noConvosMsg = document.querySelector('.no-convos-msg');
                         if (noConvosMsg) {
                             noConvosMsg.remove(); // Remove "No conversations yet" message
                             // Ensure the ul exists if it was removed
                             if (!document.getElementById('recents-list')) {
                                  const newUl = document.createElement('ul');
                                  newUl.id = 'recents-list';
                                  recentsList.parentElement.appendChild(newUl);
                             }
                         }
    
                         // Check if this convo is already in the list (shouldn't be if it's the first message)
                         const existingItem = recentsList.querySelector(`li a[href$="/assistant/${data.convo_id}/"]`);
    
                         if (!existingItem) {
                             console.log("Adding new list item to recents sidebar dynamically.");
                             const newLi = document.createElement('li');
                             const newLink = document.createElement('a');
                             newLink.href = newConvoUrl;
                             // Set temporary title ("New Chat") immediately. The final title
                             // animation will happen after the agent response animation.
                             newLink.textContent = "New Chat"; // Temporary
    
                             newLi.appendChild(newLink);
                             // Remove active class from all other list items
                             recentsList.querySelectorAll('li').forEach(li => li.classList.remove('active'));
                             newLi.classList.add('active'); // Mark the new item as active
                             recentsList.prepend(newLi); // Add to the top of the list (most recent)
                         } else {
                              console.log("List item already exists for this convo ID, ensuring active state and potential title update.");
                               // Ensure active state even if item existed (e.g., navigated back?)
                              recentsList.querySelectorAll('li').forEach(li => li.classList.remove('active'));
                              existingItem.parentElement.classList.add('active');
                               // Update title immediately if backend sends a new one that's different (less likely for first msg)
                              if (data.convo_title && existingItem.textContent.trim() !== String(data.convo_title).trim()) {
                                   existingItem.textContent = data.convo_title; // Static update for existing item
                              }
                         }
                     } else {
                          console.warn("Recents list element #recents-list not found during new convo handling.");
                     }
                  // If it was the first message, the title animation will happen after agent response
                  // via the callback in typeText below.
    
                 } else {
                      console.log("This POST was to an existing conversation URL. Ensuring active state).");
                      // Ensure the correct item is marked active if not a new convo created by this post
                      const recentsList = document.getElementById('recents-list');
                      if (recentsList && data.convo_id) {
                           const currentConvoLink = recentsList.querySelector(`li a[href$="/assistant/${data.convo_id}/"]`);
                           if (currentConvoLink) {
                               recentsList.querySelectorAll('li').forEach(li => li.classList.remove('active'));
                               currentConvoLink.parentElement.classList.add('active');
                           }
                      }
                      // For subsequent messages, update title immediately if backend sends a new one (less likely)
                       if (data.convo_id && data.convo_title && String(data.convo_title).trim() !== "New Chat") {
                            updateRecentsTitle(data.convo_id, data.convo_title); // Animated update for subsequent messages if title changes
                       }
                 }
                // --- End: Handle new conversation creation & URL update ---
    
    
                // --- START: Animate agent response and update title after animation (for the first message) ---
                if (data.agent_message_text) {
                    const plain = String(data.agent_message_text); // Ensure text is string
    
                    // Animate the agent response into the agentBubble element
                     typeText(agentBubble, plain, 5, () => {
                         // This callback runs AFTER the agent message animation finishes
                         console.log("Agent animation finished.");
                         // Check if a new AI title was generated for this conversation
                         // This only happens for the very first actual message response where AI provided text
                         if (isFirstActualMessage && data.convo_id && data.convo_title && String(data.convo_title).trim() !== "New Chat") {
                              console.log("Triggering delayed title update animation (after first agent response).");
                              updateRecentsTitle(data.convo_id, data.convo_title);
                         } else {
                              console.log("Not the first message or no new AI title to update after animation.");
                         }
                     });
    
                } else if (data.result || data.error) { // Handle non-animated responses (result/error)
                     if (data.result) {
                            agentBubble.innerHTML = `<pre>${JSON.stringify(data.result, null, 2)}</pre>`;
                     }
                     else if (data.error) {
                            agentBubble.innerHTML = `<span style="color:red">${data.error}</span>`;
                     }
                     scrollChatToBottom(); // Ensure scroll after non-animated message
    
                     // If no animation, update title immediately if it's the first message response
                     // and backend provided a new title
                     if (isFirstActualMessage && data.convo_id && data.convo_title && String(data.convo_title).trim() !== "New Chat") {
                         console.log("No agent animation (result/error). Triggering immediate title update.");
                         updateRecentsTitle(data.convo_id, data.convo_title);
                     } else {
                          console.log("Not the first message or no new AI title to update immediately.");
                     }
                } else {
                     // Handle cases where backend returns no summary, result, or error (e.g., empty response)
                     console.log("Backend response had no agent_message_text, result, or error.");
                     agentBubble.remove(); // Remove the empty agent bubble if no content
                     // If this was the first message but backend returned nothing, update title immediately with fallback
                     if (isFirstActualMessage && data.convo_id && data.convo_title) {
                         console.log("First message, but no agent content. Triggering immediate title update (likely fallback title).");
                          // Even if backend title is "New Chat", update the sidebar item to show it
                          updateRecentsTitle(data.convo_id, data.convo_title);
                     }
                }
                // --- END: Animate agent response and update title after animation ---
    
            } catch (error) {
                console.error("Error during fetch or processing response:", error);
                agentBubble.innerHTML = `<span style="color:red">An unexpected error occurred: ${error.message}</span>`;
                scrollChatToBottom(); // Ensure scroll after error message
            }
    
        });
    
        // Allow Enter (without Shift) to submit the form
        const chatInput = document.getElementById("chat-input");
        chatInput.addEventListener("keydown", e => {
            if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                form.dispatchEvent(new Event("submit", { cancelable: true, bubbles: true }));
            }
        });
    
        // --- START: Initial Page Load Rendering & Animation ---
        // On initial page load, find the welcome message if it exists and animate it.
        // All other existing messages should be rendered by Django template with markdown parsed.
    
        // Find the welcome message bubble rendered by Django (if it's a new convo page based on the flag)
    
        if (isNewConversationPageInitialLoad && welcomeMessageBubble) {
            console.log("Page loaded with welcome message. Starting animation.");
            const rawText = welcomeMessageBubble.getAttribute('data-raw');
            if (rawText) {
                // Animate the welcome message
                // typeText now handles the final scroll itself
                typeText(welcomeMessageBubble, rawText, 10); // Adjust speed here (e.g., 10ms per character)
            } else {
                console.warn("Welcome message bubble found on load, but no data-raw attribute. Rendering static markdown.");
                // Fallback: If raw data is missing, just render markdown
                if (welcomeMessageBubble.textContent) {
                    welcomeMessageBubble.innerHTML = marked.parse(welcomeMessageBubble.textContent);
                }
                 // Ensure scroll happens for the static fallback
                scrollChatToBottom();
            }
        } else {
            console.log("Page loaded without a welcome message. Rendering existing messages if any.");
            // If not a new conversation page (no welcome message rendered by Django),
            // render markdown for all agent messages that were loaded by Django.
            document.querySelectorAll('.bot-message .bubble[data-raw]').forEach(bubble => {
                const raw = bubble.getAttribute('data-raw');
                if (raw) {
                    bubble.innerHTML = marked.parse(raw);
                }
            });
             // Always scroll to bottom on page load after rendering existing messages
            scrollChatToBottom();
        }
        // --- END: Initial Page Load Rendering & Animation ---
    
    });
    