// Enable GitHub-flavored markdown and line breaks
marked.setOptions({
    gfm: true,
    breaks: true,
    smartLists: true,
    smartypants: false,
    headerIds: false,
    mangle: false
});

// Initialize conversation ID from form data attribute
document.addEventListener('DOMContentLoaded', function() {
    const chatForm = document.getElementById("chat-form");
    if (chatForm) {
        // Get the initial conversation ID from the form's data attribute
        const initialConvoId = chatForm.dataset.initialConvoId;
        if (initialConvoId) {
            // Set the conversation ID on the message input
            const messageInput = document.getElementById("chat-input");
            if (messageInput) {
                messageInput.dataset.convoId = initialConvoId;
            }
        }
    }

    // Add form submit handler
    if (chatForm) {
        chatForm.addEventListener('submit', async function(e) {
            e.preventDefault();
            const messageInput = document.getElementById("chat-input");
            const messageText = messageInput.value.trim();
            
            if (messageText) {
                // Get conversation ID from the form's data attribute
                const conversationId = chatForm.dataset.initialConvoId;
                console.log("Form submit - Using conversation ID:", conversationId);
                sendUserMessage(messageText, conversationId, messageInput);
            }
        });
    }
});

// Get the Google Calendar icon URL from a data attribute on the body
// Make sure this attribute is set in your base template or view context
// You'll need to add something like: <body data-google-calendar-icon-url="{% static 'path/to/your/google_calendar_icon.svg' %}"> in your base template or assistant.html
const googleCalendarIconUrl = document.body.dataset.googleCalendarIconUrl; // Assuming data-google-calendar-icon-url on body
const googleConnectUrl = document.body.dataset.googleConnectUrl || '/accounts/google/login/'; // Assuming data-google-connect-url on body, fallback


// --- Sidebar Toggle ---
document.querySelectorAll('.sidebar-toggle-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelector('.sidebar-wrapper').classList.toggle('collapsed');
        document.body.classList.toggle('sidebar-collapsed');
    });
});

// --- Textarea Autosize & Enter key ---
const textarea = document.getElementById("chat-input"); // Use getElementById and correct ID
if (textarea) { // Check if textarea exists
    textarea.addEventListener('input', autoResize);
    textarea.addEventListener('blur', () => {
        if (textarea.value.trim() === '') {
            textarea.style.height = '35px'; // Reset to initial minimum height
        }
    });
     textarea.addEventListener("keydown", (e) => { // Use keydown for better control
        // Check if Enter key is pressed without Shift
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault(); // Prevent newline
             const form = document.getElementById("chat-form");
             if (form) {
                 // Use requestSubmit() if available for cleaner form submission trigger
                 // Otherwise, fall back to dispatching submit event (which is caught below)
                 if (typeof form.requestSubmit === 'function') {
                     form.requestSubmit();
                 } else {
                     form.dispatchEvent(new Event("submit", { cancelable: true, bubbles: true })); // Trigger form submit
                 }
             }
        }
    });
}


function autoResize() {
    this.style.height = 'auto';
    this.style.height = this.scrollHeight + 'px';
}

// --- Scroll to Bottom ---
function scrollChatToBottom() {
    const chatMessagesContainer = document.getElementById("chat-log"); // Use getElementById
    if (chatMessagesContainer) {
        // Use smooth scrolling to the last message
        const last = chatMessagesContainer.querySelector('.message:last-child');
        if (last) {
            last.scrollIntoView({ behavior: 'smooth', block: 'end' });
        } else {
            // Fallback to instant scroll if no messages yet
            chatMessagesContainer.scrollTop = chatMessagesContainer.scrollHeight;
        }
    }
}

// --- Welcome Message Logic ---

// Function to remove the welcome message (static or animating) - Kept as it's used when sending the first message
function removeWelcomeMessage() {
    const chatMessagesContainer = document.getElementById("chat-log");
     if (!chatMessagesContainer) return;
    // Select the bubble with the data attribute and find its parent message
    const welcomeMessageBubble = chatMessagesContainer.querySelector('.message .bubble[data-welcome-message="true"]');
    if (welcomeMessageBubble) {
        const messageDiv = welcomeMessageBubble.closest('.message');
        if (messageDiv) {
             messageDiv.remove();
            console.log("Removed template-rendered welcome message.");
        }
    }
}

// Removed appendWelcomeMessageStatic function entirely - JS should not append the initial welcome message


// Function to type text character by character into a bubble element
// Includes callback for actions after typing finishes (like scrolling or title update)
// Modified to correctly handle rendering final markdown content
function typeText(element, rawText, speed = 5, callback = null) {
    let i = 0;
    const textStr = String(rawText); // Use rawText provided, ensure it's a string

    // Check if the element is still in the DOM before attempting to clear/type
    if (!document.body.contains(element)) {
        console.warn("Attempted to type text into element not in DOM. Aborting animation.");
        if (callback) {
            callback();
        }
        return;
    }

    // Clear content initially for typing animation
    element.textContent = "";
    // Add a temporary class to indicate typing is in progress (optional, for styling)
    element.classList.add('typing-in-progress');

    function nextChar() {
         // Re-check if element is still in DOM during recursion
         if (!document.body.contains(element)) {
             console.warn("Element removed from DOM during typing animation. Aborting.");
             if (callback) {
                 callback();
             }
             return;
         }

        if (i < textStr.length) {
            // Use requestAnimationFrame for smoother rendering
            requestAnimationFrame(() => {
                 element.textContent = textStr.substring(0, i + 1);
                 i++;
                 setTimeout(nextChar, speed);
            });
        } else {
            // Once typing is done, remove typing class and parse markdown
            element.classList.remove('typing-in-progress');
            element.innerHTML = marked.parse(textStr); // Parse markdown to HTML

            // Execute callback after animation finishes
            if (callback) {
                callback();
            }
             console.log("Typing animation finished.");
        }
    }
    nextChar(); // Start the animation
}

// Basic HTML escaping helper (important for injecting dynamic text into HTML)
function escapeHtml(unsafe) {
    if (!unsafe) return '';
    return unsafe
         .replace(/&/g, "&amp;")
         .replace(/</g, "&lt;")
         .replace(/>/g, "&gt;")
         .replace(/"/g, "&quot;")
         .replace(/'/g, "&#039;");
}


// --- Chat Message Appending (Handles Structured Responses) ---
// Function to create and append a message bubble or structured content
// Handles different response types based on data from the backend
// Modified to correctly transition from typing indicator to final content
function appendMessage(sender, responseData, isTyping = false, convoId = null, isFirstActualMessage = false, placeholderElement = null) {
    const chatMessagesContainer = document.getElementById("chat-log"); // Use getElementById
    if (!chatMessagesContainer) {
        console.error("Chat messages container not found!");
        return null; // Return null if container not found
    }

    const responseType = responseData?.type || 'text'; // Default to text, handle null/undefined responseData
    const responseContent = responseType === 'text' ? responseData?.response : responseData?.content; // Get content based on type


    let messageDiv;
    let contentContainer;
    let avatarDiv = null; // Initialize avatarDiv

    // If a placeholder element (the typing indicator bubble) is provided, use its parent message div
    if (placeholderElement) {
        messageDiv = placeholderElement.closest('.message');
        if (!messageDiv) {
            console.error("Placeholder element found, but its parent message div is missing!");
             // Fallback to creating a new message div if parent not found
            messageDiv = document.createElement("div");
            messageDiv.classList.add("message", sender + "-message");
        }
        // The contentContainer *is* the placeholderElement itself
        contentContainer = placeholderElement;

    } else {
         // Create a new message div if no placeholder
        messageDiv = document.createElement("div");
        messageDiv.classList.add("message", sender + "-message");

        // Create the main content container (bubble or structured box)
        contentContainer = document.createElement("div");
        // Add bubble class only for text responses initially
        if (responseType === 'text') {
           contentContainer.classList.add("bubble");
        }
    }


    // Set data attributes on the message div
    messageDiv.dataset.sender = sender;
    if (convoId) {
         messageDiv.dataset.convoId = convoId;
    }
    if (isFirstActualMessage) {
         messageDiv.dataset.isFirstActualMessage = 'true';
    }

     // Create avatar div only for standard text messages and user messages
    // Structured messages don't have avatars directly next to them
    // Check if avatarDiv exists *before* creating it again if using a placeholder
    if (!placeholderElement || messageDiv.querySelector('.avatar')) { // If not a placeholder, or if placeholder message already has avatar
        if (sender === 'user' || (sender === 'agent' && responseType === 'text')) {
             // Find existing avatar if using placeholder, otherwise create new
             avatarDiv = messageDiv.querySelector('.avatar');
             if (!avatarDiv) {
                avatarDiv = document.createElement("div");
                avatarDiv.classList.add("avatar", sender + "-avatar");
                // Prepend avatar to messageDiv if creating new
                 messageDiv.prepend(avatarDiv);
             }


             if (sender === 'user') {
                const userAvatarMain = document.querySelector('.nav-user .avatar');
                const userInitial = userAvatarMain ? userAvatarMain.textContent.trim() : 'U';
                 avatarDiv.textContent = userInitial;

            } else { // agent (for text bubbles)
                 avatarDiv.innerHTML = `
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
                      <path d="M19 4h-1V2h-2v2H8V2H6v2H5c-1.1 0-2 .9-2 2v14
                               c0 1.1.9 2 2 2h14
                               c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zM19 20H5V8h14v12z"
                            fill="#5F6368"/>
                    </svg>
                 `;
            }
        } else {
             // Remove avatar if it exists and shouldn't be there for this message type
             const existingAvatar = messageDiv.querySelector('.avatar');
             if(existingAvatar) existingAvatar.remove();
             avatarDiv = null; // Ensure avatarDiv variable is null
        }
    }


    // Handle content based on response type and typing status
    if (isTyping && responseType === 'text') {
        // If creating a new typing message, add the typing indicator HTML
         if (!placeholderElement) {
             contentContainer.innerHTML = '<div class="typing-indicator"><span></span><span></span><span></span></div>';
             messageDiv.classList.add("typing-message");
             messageDiv.appendChild(contentContainer); // Append content container if new
             chatMessagesContainer.appendChild(messageDiv); // Append message div if new
         }
        // If placeholder exists, it already has the typing indicator
        return contentContainer; // Return the content container (bubble) for typeText

    } else { // Not typing, or handling non-text response
        // Remove typing indicator class if it was present
        messageDiv.classList.remove("typing-message");
        // Remove the typing indicator HTML if it exists
        const typingIndicator = contentContainer.querySelector('.typing-indicator');
        if (typingIndicator) typingIndicator.remove();


        // Handle different response types to populate contentContainer
        if (responseType === 'text') {
            // Ensure bubble class is present for text
            contentContainer.classList.add("bubble");
            const textContent = responseContent || ''; // Get response text

            // Save raw text for potential re-rendering or copy
            messageDiv.dataset.raw = textContent;
            // Render markdown directly into the bubble
            contentContainer.innerHTML = marked.parse(textContent);

        } else if (responseType === 'event_success') {
            // Remove bubble class for structured content
            contentContainer.classList.remove("bubble");
            const eventTitle = responseContent?.event_title || 'Your Event';
            const connectedEmail = responseContent?.connected_email || '';
            // Use the globally available googleCalendarIconUrl variable
            const eventSuccessHtml = `
                 <div class="create-event-box">
                    <div class="create-event-icon">
                       ${googleCalendarIconUrl ? `<img src="${googleCalendarIconUrl}" alt="Google Calendar" width="24" height="24">` : ''}
                       <svg width="18" height="18" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                         <path d="M12 2C6.48 2 2 6.48 2 12C2 17.52 6.48 22 12 22C17.52 22 22 17.52 22 12C22 6.48 17.52 2 12 2ZM10 17L5 12L6.41 10.59L10 14.17L17.59 6.58L19 8L10 17Z" fill="#34A853"/>
                       </svg>
                    </div>
                    <div class="create-event-details">
                       <div class="create-event-title">${escapeHtml(eventTitle)}</div> {# Escape title #}
                       <div class="create-event-subtitle success">Event created successfully</div>
                    </div>
                 </div>
              `;
            contentContainer.innerHTML = eventSuccessHtml;

        } else if (responseType === 'needs_connection') {
            // Remove bubble class for structured content
            contentContainer.classList.remove("bubble");
            const connectedEmail = responseContent?.email || '';
            const needsConnectionHtml = `
                 <div class="connect-account-section">
                   <p class="connect-account-heading">Please connect your Google account.</p>
                   <div class="connect-buttons">
                      <a href="${googleConnectUrl}" class="google-connect-btn">
                         <svg width="18" height="18" viewBox="0 0 18 18" fill="none" xmlns="http://www.w3.org/2000/svg">
                            <path d="M17.64 9.2045C17.64 8.56567 17.5844 7.95816 17.4769 7.37974H9V10.7197H13.9183C13.6711 12.0829 12.9344 13.2644 11.8344 14.0153V16.2715H14.7776C16.5322 14.6384 17.64 12.2644 17.64 9.2045Z" fill="#4285F4"/>
                            <path d="M9 18C11.43 18 13.46 17.19 14.96 15.94L11.83 14.01C11.09 14.5 10.1 14.81 9 14.81C6.81 14.81 4.96 13.45 4.36 11.54H1.33V13.8C2.84 16.85 5.62 18 9 18Z" fill="#34A853"/>
                            <path d="M4.36 11.54C4.08 10.85 3.93 10.08 3.93 9.29C3.93 8.5 4.08 7.73 4.36 7.04V4.78H1.33C0.48 6.47 0 7.88 0 9.29C0 10.7 0.48 12.11 1.33 13.8L4.36 11.54Z" fill="#FBBC05"/>
                            <path d="M9 3.87C10.14 3.87 11.15 4.26 11.96 5.05L15.01 2.01C13.46 0.76 11.43 0 9 0C5.62 0 2.84 1.15 1.33 4.19L4.36 6.46C4.96 4.55 6.81 3.19 9 3.19V3.87Z" fill="#EA4335"/>
                         </svg>
                         Connect ${escapeHtml(connectedEmail) || 'your Google account'}
                      </a>
                      <button class="skip-btn">Skip</button>
                   </div>
                 </div>
               `;
            contentContainer.innerHTML = needsConnectionHtml;

        } else if (responseType === 'connected_status') {
            // Remove bubble class for structured content
            contentContainer.classList.remove("bubble");
            const connectedEmail = responseContent?.email || 'Account';
            const connectedStatusHtml = `
                 <div class="connected-account-status">
                   <svg width="18" height="18" viewBox="0 0 18 18" fill="none" xmlns="http://www.w3.org/2000/svg">
                      <path d="M17.64 9.2045C17.64 8.56567 17.5844 7.95816 17.4769 7.37974H9V10.7197H13.9183C13.6711 12.0829 12.9344 13.2644 11.8344 14.0153V16.2715H14.7776C16.5322 14.6384 17.64 12.2644 17.64 9.2045Z" fill="#4285F4"/>
                      <path d="M9 18C11.43 18 13.46 17.19 14.96 15.94L11.83 14.01C11.09 14.5 10.1 14.81 9 14.81C6.81 14.81 4.96 13.45 4.36 11.54H1.33V13.8C2.84 16.85 5.62 18 9 18Z" fill="#34A853"/>
                      <path d="M4.36 11.54C4.08 10.85 3.93 10.08 3.93 9.29C3.93 8.5 4.08 7.73 4.36 7.04V4.78H1.33C0.48 6.47 0 7.88 0 9.29C0 10.7 0.48 12.11 1.33 13.8L4.36 11.54Z" fill="#FBBC05"/>
                      <path d="M9 3.87C10.14 3.87 11.15 4.26 11.96 5.05L15.01 2.01C13.46 0.76 11.43 0 9 0C5.62 0 2.84 1.15 1.33 4.19L4.36 6.46C4.96 4.55 6.81 3.19 9 3.19V3.87Z" fill="#EA4335"/>
                   </svg>
                   <span class="connected-text">${escapeHtml(connectedEmail)} connected</span>
                   <svg width="18" height="18" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                     <path d="M9 16.17L5.41 12.59L4 14L9 19L20 8L18.59 6.59L9 16.17Z" fill="#9AA0A6"/>
                   </svg>
                 </div>
               `;
            contentContainer.innerHTML = connectedStatusHtml;

        }
        // Add logic for 'thinking' or other future types here
        // else if (responseType === 'thinking') { ... }

        // Append messageDiv and contentContainer if they are new
        if (!placeholderElement) {
             // Avatar prepended earlier if needed
             messageDiv.appendChild(contentContainer);
             chatMessagesContainer.appendChild(messageDiv);
        }

         // Scroll to bottom after appending/updating
         scrollChatToBottom();

        return null; // Return null for non-typing messages
    }
}


// Function to update the conversation title in the recents list
function updateRecentsTitle(convoId, newTitle) {
     const recentsList = document.getElementById('recents-list'); // Ensure recentsList is accessible
     if (!recentsList) {
         console.warn("Recents list not found, cannot update title.");
         return; // Exit if recents list not found
     }

     // Find the list item using data-convo-id
     const recentItem = recentsList.querySelector(`li[data-convo-id="${convoId}"]`);
     if (recentItem) {
         const recentLink = recentItem.querySelector('.recent-link'); // Assuming the link has class 'recent-link'
         if (recentLink) {
             // Use textContent to avoid rendering HTML from the title
             const plainTitle = String(newTitle || "New Chat").trim();
             // Simple update without animation for now
             recentLink.textContent = plainTitle;

             // Ensure active state
             recentsList.querySelectorAll('li').forEach(li => li.classList.remove('active'));
             recentItem.classList.add('active');

              // Move to top (optional, but common for most recent)
             recentsList.prepend(recentItem); // Move the updated item to the top
             console.log(`Updated title for convo ID ${convoId} to "${plainTitle}".`);
         } else {
              console.warn(`Link element with class 'recent-link' not found within list item for convo ID ${convoId}.`);
         }
     } else {
          console.warn(`List item with data-convo-id="${convoId}" not found in recents list.`);
     }
}


// --- Main DOMContentLoaded listener ---
document.addEventListener("DOMContentLoaded", () => {
    const chatMessagesContainer = document.getElementById("chat-log"); // Use getElementById
    const form = document.getElementById("chat-form"); // Use getElementById
    const recentsList = document.getElementById('recents-list'); // Use getElementById
    const initialMessagesOnLoad = chatMessagesContainer ? chatMessagesContainer.querySelectorAll('.message') : [];


    // --- Initial Page Load Rendering & Welcome Message Handling ---
    console.log("DOMContentLoaded: Checking for initial messages to render/animate.");
     // Re-query initial messages to ensure we get the correct elements after DOM load
    //  const initialMessagesOnLoad = chatMessagesContainer ? chatMessagesContainer.querySelectorAll('.message') : [];


     if (initialMessagesOnLoad.length > 0) {
         console.log(`Found ${initialMessagesOnLoad.length} initial messages.`);
         let welcomeMessageFoundAndAnimated = false;

         initialMessagesOnLoad.forEach(messageDiv => {
             const sender = messageDiv.dataset.sender;
             const rawText = messageDiv.dataset.raw;
             const bubble = messageDiv.querySelector('.bubble'); // Get the bubble inside the message

             if (bubble) { // Check if bubble exists
                 // If it's the welcome message rendered by Django, animate it
                 if (bubble.dataset.welcomeMessage === 'true') { // Check for the data attribute on the bubble
                      console.log("Found initial welcome message (from template). Starting animation.");
                      welcomeMessageFoundAndAnimated = true;
                      // Use typeText for animation. Pass scrollChatToBottom as callback.
                      // Pass the raw text for animation and final rendering
                      typeText(bubble, rawText || bubble.innerHTML, 10, scrollChatToBottom); // Use data-raw or innerHTML as fallback
                 } else if (sender === 'agent' && rawText !== undefined) {
                      // Render markdown for other agent messages statically if raw text is available
                      bubble.innerHTML = marked.parse(rawText);
                 } else if (sender === 'user') {
                      // Render plain text for user messages statically
                       bubble.textContent = rawText || bubble.textContent; // Use raw or existing text
                 }
            } else {
                 // Handle structured message types rendered by template on load if needed
                 // Currently, only text messages (including welcome) are rendered by template
                 // Add logic here if other types (like needs_connection) can be pre-rendered
                 console.warn("Message div found without a bubble element during initial render:", messageDiv);
                 // If no bubble but raw text exists, just add it plain for now
                 if (rawText !== undefined) {
                      messageDiv.textContent = rawText;
                 }
            }
         });

         // Ensure scroll to bottom after initial render/animation setup, but only if no welcome animation started.
          // If welcome animation started, the callback handles the scroll.
          if (!welcomeMessageFoundAndAnimated) {
              scrollChatToBottom();
          }

     } else {
        //  This else block is for when no initial messages are found *at all* from the template render.
        //  With the current view logic, this block should ideally not be hit when `is_new_conversation_page` is true
        //  because the view always adds a temporary welcome message in that case.
        //  If you change the view to *not* render the temporary message, you would need JS to add it here.
         console.log("No initial messages found from template.");
         scrollChatToBottom(); // Scroll to ensure input is visible
     }


    // --- Handle form submission ---
    if (form) {
        form.addEventListener("submit", async e => {
            e.preventDefault();

            const textarea = document.getElementById("chat-input"); // Use getElementById and correct ID
            const userMessage = textarea ? textarea.value.trim() : '';

            if (!userMessage) {
                // Clear textarea and reset height for empty message
                if (textarea) {
                    textarea.value = '';
                    textarea.style.height = 'auto';
                }
                return; // Don't send empty messages
            }

            // Get the current conversation ID from the browser URL
            let conversationId = null;
            const pathParts = window.location.pathname.split('/').filter(part => part); // Split and remove empty parts
             // Assuming URL structure is /agent/assistant/UUID/ or /agent/assistant/new/
            // Find the UUID part in the URL
            const uuidRegex = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
             for (let i = 0; i < pathParts.length; i++) {
                 if (uuidRegex.test(pathParts[i])) {
                     conversationId = pathParts[i];
                     break;
                 }
             }
             // If the last part is 'new', convoId remains null here, which is correct for creating a new convo

            // Get the POST URL from the form action (set to chat_process)
            const postUrl = form.action; // This is now correctly set to {% url 'home_page:chat_process' %}


            // Immediately append user message
            // For user messages, responseData will just contain the text
            appendMessage("user", { type: 'text', response: userMessage }, false, conversationId, false); // isTyping=false for user message
            if (textarea) {
                 textarea.value = ''; // Clear the textarea
                 textarea.style.height = 'auto'; // Reset textarea height
            }


            // Remove the welcome message if it's present (only happens on the very first message in an empty convo)
            // Do this AFTER appending the user message and before appending the agent's placeholder
            removeWelcomeMessage();


            // Append a placeholder for the agent's reply (potential typing indicator)
            // Pass a minimal responseData object to indicate expected type (text) for typing
            const typingMessageElementPlaceholderBubble = appendMessage("agent", { type: 'text', response: '' }, true, conversationId, false); // Append typing indicator


            try {
                const csrfTokenInput = form.querySelector("[name=csrfmiddlewaretoken]");
                const csrfToken = csrfTokenInput ? csrfTokenInput.value : '';

                // POST data as JSON (preferred over form-urlencoded for complex data)
                const response = await fetch(postUrl, { // Use the dedicated POST URL
                        method: "POST",
                    headers: {
                        "Content-Type": "application/json", // Send as JSON
                        "X-CSRFToken": csrfToken,
                    },
                    // Send message text and conversation ID in the JSON body
                    body: JSON.stringify({ message: userMessage, conversation_id: conversationId }),
                });

                if (!response.ok) {
                     console.error("Fetch failed with status:", response.status, response.statusText);
                     // Remove placeholder and show an error message
                     if (typingMessageElementPlaceholderBubble) {
                         const parentMessageDiv = typingMessageElementPlaceholderBubble.closest('.message');
                         if(parentMessageDiv) parentMessageDiv.remove();
                     }
                     // Append error as a text message
                     appendMessage("agent", { type: 'text', response: `Error communicating with the server (${response.status}). Please try again.` }, false, conversationId, false);
                     return;
                }

                const data = await response.json();
                console.log("AJAX response data received:", data);

                 // --- Handle Agent's Structured Response ---
                if (data.agent_response_data) {
                     const agentResponse = data.agent_response_data;
                    const responseType = agentResponse.type || 'text';
                    const responseContent = responseType === 'text' ? agentResponse.response : agentResponse.content;

                    // Determine if it's the first message exchange *based on the backend response*
                    const isFirstActualMessageFromBackend = data.is_first_actual_message === true;

                    // Use the existing placeholder element for the agent's response
                    // Pass the placeholder bubble element to appendMessage
                     appendMessage("agent", agentResponse, false, data.conversation_id, isFirstActualMessageFromBackend, typingMessageElementPlaceholderBubble); // isTyping=false, pass placeholder


                    // --- START: Handle new conversation creation & URL update (if it was the first message) ---
                     // This block runs if the backend confirmed it was the first message and created a new convo
                    if (isFirstActualMessageFromBackend && data.conversation_id) {
                        console.log("Backend created/identified a new conversation with this message. Updating URL and sidebar.");

                        // Update the browser URL to the new conversation
                        const newConvoUrl = `/agent/assistant/${data.conversation_id}/`;
                        window.history.pushState({}, data.title || 'New Chat', newConvoUrl);

                        // Reload the page to sync the sidebar and chat with the backend
                        window.location.reload();
                        return; // Prevent further JS updates since reload is happening
                    } else {
                         console.log("This POST was to an existing conversation. Ensuring active state).");
                         // Ensure the correct item is marked active if not a new convo created by this post
                         const recentsList = document.getElementById('recents-list');
                         if (recentsList && data.conversation_id) {
                              const currentConvoItem = recentsList.querySelector(`li[data-convo-id="${data.conversation_id}"]`);
                              if (currentConvoItem) {
                                  recentsList.querySelectorAll('li').forEach(li => li.classList.remove('active'));
                                  currentConvoItem.classList.add('active');
                                  // Move to top (optional)
                                  recentsList.prepend(currentConvoItem);
                              }
                         }
                         // Title update for subsequent messages is handled above based on responseType
                    }
                   // --- End: Handle new conversation creation & URL update ---


                } else if (data.error) {
                    // Display error message if backend sends one
                     // Remove typing indicator placeholder if it wasn't already removed
                    if (typingMessageElementPlaceholderBubble) {
                         const parentMessageDiv = typingMessageElementPlaceholderBubble.closest('.message');
                         if(parentMessageDiv) parentMessageDiv.remove();
                    }
                    // Append error as a text message
                    appendMessage("agent", { type: 'text', response: `Error: ${data.error}` }, false, data.conversation_id, data.is_first_actual_message); // Pass backend flag

                     // If it was the first message but backend returned an error, update title immediately with fallback
                     if (data.is_first_actual_message && data.conversation_id && data.title) {
                         console.log("First message, but backend returned error. Triggering immediate title update (likely fallback title).");
                          updateRecentsTitle(data.conversation_id, data.title);
                     } else if (data.conversation_id && data.title) {
                          updateRecentsTitle(data.conversation_id, data.title);
                     }

                } else {
                     // Handle cases where backend returns no agent_response_data or error
                     console.log("Backend response had no agent_response_data or error.");
                      // Remove typing indicator placeholder
                     if (typingMessageElementPlaceholderBubble) {
                         const parentMessageDiv = typingMessageElementPlaceholderBubble.closest('.message');
                         if(parentMessageDiv) parentMessageDiv.remove();
                     }
                      // Maybe append a generic message or just remove the placeholder.
                     // appendMessage("agent", { type: 'text', response: "Received empty response from agent." }, false);
                     // If it was the first message but backend returned nothing useful, update title
                      if (data.is_first_actual_message && data.conversation_id && data.title) {
                         console.log("First message, but no agent response. Triggering immediate title update (likely fallback title).");
                          updateRecentsTitle(data.conversation_id, data.title);
                     } else if (data.conversation_id && data.title) {
                          updateRecentsTitle(data.conversation_id, data.title);
                     }
                }


            } catch (error) {
                console.error("Error during fetch or processing response:", error);
                 // Remove typing indicator placeholder
                if (typingMessageElementPlaceholderBubble) {
                     const parentMessageDiv = typingMessageElementPlaceholderBubble.closest('.message');
                     if(parentMessageDiv) parentMessageDiv.remove();
                }
                // Append a generic error message as text
                appendMessage("agent", { type: 'text', response: `Sorry, an unexpected error occurred: ${error.message}` }, false, conversationId, false);

                 // If it was the first message but there was a JS error, update title with fallback if possible
                 // This requires the convo ID to be available in the initial page context or the POST body
                 const currentConvoIdFromUrl = new URL(window.location.href).pathname.split('/').filter(part => part).pop(); // Get last non-empty part of path
                 // Check if it's a valid-looking UUID before using it
                 const uuidRegex = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
                 const errorConvoId = (currentConvoIdFromUrl && uuidRegex.test(currentConvoIdFromUrl)) ? currentConvoIdFromUrl : (data && data.conversation_id ? data.conversation_id : null);

                 if (errorConvoId && data && data.title) { // Check if convoId is valid and data object exists and has title
                      console.log("JS error on first message. Triggering immediate title update (fallback).");
                       updateRecentsTitle(errorConvoId, data.title); // Use fallback title from potential data or just "New Chat"
                 } else if (errorConvoId) {
                     // If we have a convo ID but no title in the error response data, use a generic fallback
                     updateRecentsTitle(errorConvoId, "Error occurred");
                 }


            }
        });
    }

    // --- Initial Page Load Logic Execution ---
     // This section handles animating the template-rendered welcome message if it exists.
     console.log("DOMContentLoaded: Checking for initial messages to render/animate.");
     // Re-query initial messages to ensure we get the correct elements after DOM load


     if (initialMessagesOnLoad.length > 0) {
         console.log(`Found ${initialMessagesOnLoad.length} initial messages.`);
         let welcomeMessageFoundAndAnimated = false;

         initialMessagesOnLoad.forEach(messageDiv => {
             const sender = messageDiv.dataset.sender;
             const rawText = messageDiv.dataset.raw;
             const bubble = messageDiv.querySelector('.bubble'); // Get the bubble inside the message

             if (bubble) { // Check if bubble exists
                 // If it's the welcome message rendered by Django, animate it
                 if (bubble.dataset.welcomeMessage === 'true') { // Check for the data attribute on the bubble
                      console.log("Found initial welcome message (from template). Starting animation.");
                      welcomeMessageFoundAndAnimated = true;
                      // Use typeText for animation. Pass scrollChatToBottom as callback.
                      // Pass the raw text for animation and final rendering
                      typeText(bubble, rawText || bubble.innerHTML, 10, scrollChatToBottom); // Use data-raw or innerHTML as fallback
                 } else if (sender === 'agent' && rawText !== undefined) {
                      // Render markdown for other agent messages statically if raw text is available
                      bubble.innerHTML = marked.parse(rawText);
                 } else if (sender === 'user') {
                      // Render plain text for user messages statically
                       bubble.textContent = rawText || bubble.textContent; // Use raw or existing text
                 }
            } else {
                 // Handle structured message types rendered by template on load if needed
                 // Currently, only text messages (including welcome) are rendered by template
                 // Add logic here if other types (like needs_connection) can be pre-rendered
                 console.warn("Message div found without a bubble element during initial render:", messageDiv);
                 // If no bubble but raw text exists, just add it plain for now
                 if (rawText !== undefined) {
                      messageDiv.textContent = rawText;
                 }
            }
         });

         // Ensure scroll to bottom after initial render/animation setup, but only if no welcome animation started.
          // If welcome animation started, the callback handles the scroll.
          if (!welcomeMessageFoundAndAnimated) {
              scrollChatToBottom();
          }

     } else {
        //  This else block is for when no initial messages are found *at all* from the template render.
        //  With the current view logic, this block should ideally not be hit when `is_new_conversation_page` is true
        //  because the view always adds a temporary welcome message in that case.
        //  If you change the view to *not* render the temporary message, you would need JS to add it here.
         console.log("No initial messages found from template.");
         scrollChatToBottom(); // Scroll to ensure input is visible
     }


});

// Helper to force DOM reflow/repaint
function forceReflow(element) {
    void element.offsetHeight; // Reading this property forces reflow
}

// Function to send user message and handle AI response
function sendUserMessage(messageText, conversationId, inputElement) {
    // If no inputElement is provided, try to get it
    if (!inputElement) {
        inputElement = document.getElementById('chat-input');
    }

    // If no conversationId is provided, try to get it from the input element's dataset
    if (!conversationId && inputElement && inputElement.dataset && inputElement.dataset.convoId) {
        conversationId = inputElement.dataset.convoId;
    }

    // If still no conversationId, try to get it from the form
    if (!conversationId) {
        const chatForm = document.getElementById("chat-form");
        if (chatForm && chatForm.dataset && chatForm.dataset.initialConvoId) {
            conversationId = chatForm.dataset.initialConvoId;
        }
    }
    
    // For new conversations, we don't need a conversationId
    // The backend will create one for us
    console.log("sendUserMessage called with:", { messageText, conversationId, hasInputElement: !!inputElement });
    
    // Show the user message immediately
    const chatBox = $('#chat-box');
    const userMessageHtml = `
        <div class="message user-message">
            <div class="avatar user-avatar">
                ${userAvatar}
            </div>
            <div class="message-content">
                <div class="message-bubble">
                    <div class="message-text">${escapeHtml(messageText).replace(/\n/g, '<br>')}</div>
                </div>
            </div>
        </div>`;
    const userMessageElement = $(userMessageHtml);
    chatBox.append(userMessageElement);

    // Use requestAnimationFrame to ensure the element is painted before proceeding
    requestAnimationFrame(() => {
        // Wait for another frame to allow styles to fully apply after the first paint
        requestAnimationFrame(() => {
            // Now that the message element is likely rendered correctly,
            // proceed with showing the thinking indicator and scrolling.

            // Scroll to the bottom
            chatBox.scrollTop(chatBox[0].scrollHeight);

            // Show a temporary "..." or loading indicator for the agent
            const agentThinkingHtml = `
                <div id="agent-thinking" class="message agent-message">
                     <div class="avatar agent-avatar">
                        ${agentAvatar}
                    </div>
                    <div class="message-content">
                        <div class="message-bubble">
                            <div class="message-text"></div>
                        </div>
                    </div>
                </div>`;
            chatBox.append(agentThinkingHtml);
            chatBox.scrollTop(chatBox[0].scrollHeight);

            // Get CSRF token and send message via AJAX
            const csrftoken = document.querySelector('[name=csrfmiddlewaretoken]').value;
            $.ajax({
                url: '/agent/chat/process/',
                type: 'POST',
                contentType: 'application/json',
                data: JSON.stringify({
                    message: messageText,
                    convo_id: conversationId || null // Send null if no conversationId
                }),
                dataType: 'json',
                headers: {
                    'X-CSRFToken': csrftoken
                },
                success: function(response) {
                    console.log("Message sent successfully, processing response:", response);
                    // Remove the thinking indicator
                    $('#agent-thinking').remove();
                
                    // --- CORRECTED: Handle the response data (check top-level keys) ---
                    if (response.type === 'calendar_action_request') {
                        // Handle structured calendar action request
                        const agentResponse = response; // The response itself contains the data
                        displayAgentMessage(agentResponse.content.message_for_user || "Processing calendar action..."); // Display introductory message
                        handleCalendarActionRequest(agentResponse.content); // Show the action UI (ensure handleCalendarActionRequest is defined)
                
                    } else if (response.type === 'text') {
                        // Handle a standard text response
                        const agentResponse = response; // The response itself contains the data
                        displayAgentMessage(agentResponse.response); // Display the AI's text response (ensure displayAgentMessage is defined)
                
                    } else if (response.error) {
                        // Handle error sent by the backend
                        const agentResponse = response; // The response itself contains the error
                        appendMessage("agent", { type: 'text', response: `Error: ${agentResponse.error}` }, false, response.conversation_id, response.is_first_actual_message); // Use appendMessage for errors (ensure appendMessage is defined)
                
                    } else {
                         // Handle cases where backend returns an unexpected structure
                         console.warn("Backend response had no expected type ('text' or 'calendar_action_request') or error.", response);
                         // Optionally display a generic message or just remove the placeholder
                         // appendMessage("agent", { type: 'text', response: "Received an unexpected response from the agent." }, false, response.conversation_id, response.is_first_actual_message);
                         // If placeholder is still there, remove it.
                         $('#agent-thinking').remove(); // Should already be removed above, but safety
                    }
                    // --- END CORRECTED Handling ---
                
       
                   // --- Handle New Conversation Creation & Recents Update (if it was the first message) ---
                   // This logic runs if the backend confirmed it was the first message and created a new convo
                   if (response.is_first_actual_message && response.conversation_id) {
                       console.log("Backend created/identified a new conversation with this message. Updating URL and sidebar.");
       
                       // Update the browser URL to the new conversation
                       const newConvoUrl = `/agent/assistant/${response.conversation_id}/`;
                       window.history.pushState({}, response.convo_title || 'New Chat', newConvoUrl);
       
                       // Reload the page to sync the sidebar and chat with the backend
                       window.location.reload();
                       return; // Prevent further JS updates since reload is happening
       
                   } else if (response.conversation_id) {
                        // If it was a subsequent message to an existing convo, just ensure the active state is correct
                        const recentsList = $('#recents-list');
                        recentsList.find('li').removeClass('active');
                        const currentConvoItem = recentsList.find(`li[data-convo-id="${response.conversation_id}"]`);
                        if (currentConvoItem.length) {
                             currentConvoItem.addClass('active');
                             // Move to top (optional)
                            recentsList.prepend(currentConvoItem);
                        }
       
                        // Update title for subsequent messages if backend sends a new one
                        if (response.title) {
                             updateRecentsTitle(response.conversation_id, response.title); // Ensure updateRecentsTitle is defined
                        }
       
                   }
       
       
                   // Final scroll to the bottom after all messages/elements are added
                   chatBox.scrollTop(chatBox[0].scrollHeight);
       
                   // Clear the input field using the passed element
                   if (inputElement) {
                       inputElement.value = '';
                       console.log("Input field cleared successfully in sendUserMessage success.");
                   }
       
                   // Find the placeholder
                   const placeholderLi = $('#recents-list li[data-placeholder="true"]');
                   if (placeholderLi.length && response.conversation_id) {
                       // Remove the placeholder
                       placeholderLi.remove();

                       // Create the new conversation li
                       const newLiHtml = `
                           <li data-convo-id="${response.conversation_id}" data-delete-url="/agent/assistant/delete_conversation/${response.conversation_id}/" class="active animate__animated animate__fadeIn">
                               <a href="/agent/assistant/${response.conversation_id}/" class="recent-link">${escapeHtml(response.convo_title || 'New Chat')}</a>
                               <button class="delete-recent-btn">
                                   <img src="/static/home_page/images/delete.png" alt="Delete">
                               </button>
                           </li>
                       `;
                       // Remove active from all others
                       recentsList.find('li').removeClass('active');
                       // Prepend and animate
                       const $newLi = $(newLiHtml).hide();
                       recentsList.prepend($newLi);
                       $newLi.fadeIn(300);

                       // Optionally, scroll into view
                       $newLi[0].scrollIntoView({ behavior: 'smooth', block: 'nearest' });

                       // Re-attach delete handler
                       $newLi.find('.delete-recent-btn').on('click', function(e) {
                           e.preventDefault();
                           e.stopPropagation();
                           // ... existing delete handler code ...
                       });
                   }
       
                },
                error: function(xhr, status, error) {
                     // Remove the thinking indicator
                    $('#agent-thinking').remove();
                    // Display an error message
                    let errorMessage = 'An error occurred while sending your message.'; // Generic error for sending
                    if (xhr.status === 403) {
                        errorMessage = 'You do not have permission to send messages.';
                    } else if (xhr.responseJSON && xhr.responseJSON.error) {
                        errorMessage = 'Error: ' + xhr.responseJSON.error;
                    } else {
                        errorMessage += ` Status: ${xhr.status}`;
                    }
                   alert(errorMessage);
                   console.error("Message sending failed.", status, error, xhr);
       
                   // Do not clear the input on error so the user can retry/edit
                }
             });
        });
    });
}

// Function to display agent message with typing animation and markdown support
function displayAgentMessage(messageText) {
    const chatBox = $('#chat-box');
    const agentMessageHtml = `
        <div class="message agent-message new-message">
             <div class="avatar agent-avatar">
                ${agentAvatar}
            </div>
            <div class="message-content">
                <div class="message-bubble">
                    <div class="message-text"></div> <!-- Text will be typed here -->
                </div>
            </div>
        </div>`;
    chatBox.append(agentMessageHtml);

    const newMessage = chatBox.find('.new-message').last();
    const textElement = newMessage.find('.message-text'); // Target the .message-text div

    // Parse the markdown to HTML
    const parsedMessageHtml = marked.parse(messageText);

    // Create a temporary element to extract plain text content from the parsed HTML
    const tempDiv = $('<div>').html(parsedMessageHtml);
    const plainTextContent = tempDiv.text(); // Get only the text content

    let i = 0;
    const typingSpeed = 3; // milliseconds (adjust as desired for animation speed)

    function typeWriter() {
        if (i < plainTextContent.length) {
            // Append characters of the plain text content for animation
            textElement.append(plainTextContent.charAt(i));
            i++;
            chatBox.scrollTop(chatBox[0].scrollHeight);
            setTimeout(typeWriter, typingSpeed);
        } else {
            // Animation finished - replace the plain text with the full parsed HTML
            textElement.html(parsedMessageHtml); // <-- Replace with HTML
            newMessage.removeClass('new-message'); // Remove class after animation
            chatBox.scrollTop(chatBox[0].scrollHeight); // Final scroll
        }
    }

    // Start the typing animation
    typeWriter();

}

// Function to handle the calendar action request
function handleCalendarActionRequest(content) {
    const chatBox = $('#chat-box');
    // Remove any existing action UIs before adding a new one
    $('.calendar-action-ui').remove();

    let actionUiHtml = '';

    if (content.needs_connection) {
        // Show the connect Google account prompt
        actionUiHtml = `
            <div class="message agent-message calendar-action-ui">
                 <div class="message-avatar agent-avatar">
                    ${agentAvatar}
                </div>
                <div class="message-content">
                    <div class="message-bubble">
                        <p>Please connect your Google account to manage your calendar.</p>
                        <a href="${content.connect_url}" class="btn btn-primary btn-sm google-connect-button">Connect Google Account</a>
                         <button class="btn btn-secondary btn-sm skip-button">Skip</button>
                    </div>
                </div>
            </div>`;
         chatBox.append(actionUiHtml);

         // Add event listener to the skip button if needed (optional based on requirements)
        $('.skip-button').on('click', function() {
            // Handle skip logic here, e.g., remove the UI, send a message back to the agent
            $(this).closest('.calendar-action-ui').remove();
            displayAgentMessage("Okay, skipping the calendar action for now.");
        });


    } else if (content.create_event_form) {
         // Show the create event form (this part needs to be implemented based on how the form is rendered/sent)
         // For now, let's just display a message indicating a form should appear.
         displayAgentMessage("Okay, I'm ready to create the event. (Form display needs implementation)");
          // Ideally, the server would send HTML for the form, or the JS would dynamically create it
         // based on the 'create_event_form' data structure if provided.
         // Example (conceptual):
         // const formHtml = buildCreateEventForm(content.create_event_form_data);
         // chatBox.append(`<div class="calendar-action-ui">${formHtml}</div>`);

    }
     // Add other action types here (e.g., confirmation, details forms)

    chatBox.scrollTop(chatBox[0].scrollHeight);
}

function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== '') {
        const cookies = document.cookie.split(';');
        for (let i = 0; i < cookies.length; i++) {
            const cookie = cookies[i].trim();
            // Does this cookie string begin with the name we want?
            if (cookie.startsWith(name + '=')) {
                cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                break;
            }
        }
    }
    return cookieValue;
}


$(document).ready(function() {
    const messageInput = $('#chat-input');
    const sendButton = $('#send-button');
    const chatForm = $('#chat-form');
    const chatBox = $('#chat-box'); // Get chat-box element
    const recentsList = $('#recents-list');


    // Get user and agent avatars from data attributes (set in the template)
    window.userAvatar = chatForm.data('user-avatar') || '';
    // In home.js, likely near the top or inside $(document).ready(...)
    window.agentAvatar = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none"><path d="M19 4h-1V2h-2v2H8V2H6v2H5c-1.1 0-2 .9-2 2v14 c0 1.1.9 2 2 2h14 c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zM19 20H5V8h14v12z" fill="#5F6368"/></svg>';
    // Initial scroll to the bottom
    chatBox.scrollTop(chatBox[0].scrollHeight);

    // --- Welcome Message Animation on Initial Page Load / New Chat ---
    const body = $('body');
    const isNewConversationPage = body.data('is-new-conversation');
    const welcomeMessageText = body.data('welcome-message-text');

    console.log("Document ready. isNewConversationPage:", isNewConversationPage);
    console.log("Welcome message text from data attribute:", welcomeMessageText);

    if (isNewConversationPage && welcomeMessageText) {
        console.log("New conversation page detected, animating welcome message.");

        // Dynamically create the HTML for the agent's welcome message
        const welcomeMessageHtml = `
            <div class="message agent-message">
              <div class="avatar agent-avatar">
                 ${window.agentAvatar}
              </div>
              <div class="message-content">
                 <div class="message-bubble">
                     <div class="message-text">
                         
                     </div>
                 </div>
              </div>
            </div>
        `;

        // Append the created HTML to the chat box
        chatBox.append(welcomeMessageHtml);
        console.log("Welcome message HTML appended.");

        // Find the message-text element within the newly added message
        const newMessageElement = chatBox.find('.agent-message:last .message-text');
        console.log("New message element found:", newMessageElement.length > 0);

        if (newMessageElement.length) {
             // Unescape HTML entities in the welcome message text before typing
             // Using jQuery's text() method to unescape
             const unescapedWelcomeText = $('<div>').html(welcomeMessageText).text();
             console.log("Unescaped welcome message text:", unescapedWelcomeText);


            // Use your existing typing animation logic
            let i = 0;
            const typingSpeed = 3; // Adjust as desired
            // Use the unescaped text for typing
            const fullText = unescapedWelcomeText;

            function typeWriter() {
                if (i < fullText.length) {
                    // Append one character at a time
                    newMessageElement.append(fullText.charAt(i));
                    i++;
                    // Scroll to bottom after appending each character
                    chatBox.scrollTop(chatBox[0].scrollHeight);
                    setTimeout(typeWriter, typingSpeed);
                } else {
                     // Typing finished. Final scroll.
                     newMessageElement.html(marked.parse(fullText)); // Apply markdown parsing
                     chatBox.scrollTop(chatBox[0].scrollHeight);
                     console.log("Typing animation finished.");
                }
            }
            typeWriter(); // Start the animation

        } else {
            console.error("Could not find the message-text element for welcome message animation.");
        }
    }
    // --- END Welcome Message Animation ---

    // --- Markdown Rendering for Messages Loaded from Database ---
    // This should target messages that were NOT just animated (i.e., existing messages loaded from DB)
    // Ensure this section doesn't process the welcome message if it's present in the initial HTML for some reason.
    // The current check using [data-raw] is good if only DB messages have this.
    chatBox.find('.agent-message .message-bubble[data-raw]').each(function() {
       const rawText = $(this).data('raw');
        if (rawText !== undefined) {
            $(this).html(marked.parse(String(rawText)));
        }
    });
    // --- End Markdown Rendering for DB Messages ---

    // ... rest of your JS code (input clearing, event handlers, sendUserMessage function etc.) ...

    // Handle sending message on button click
    sendButton.on('click', function() {
        const form = document.getElementById('chat-form');
        if (form && typeof form.requestSubmit === 'function') {
            form.requestSubmit();
        }
    });

    // Handle sending message on pressing Enter key
    messageInput.on('keydown', function(e) {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            const form = document.getElementById('chat-form');
            if (form && typeof form.requestSubmit === 'function') {
                form.requestSubmit();
            }
        }
    });

    recentsList.on('click', '.delete-recent-btn', function(e){
        e.preventDefault(); // Prevent the default link behavior of the parent li/a
        e.stopPropagation(); // Prevent the click from bubbling up to the list item's link

        const listItem = $(this).closest('li'); // Get the parent li element
        const convoId = listItem.data('convo-id'); // Get the conversation ID from data attribute
        const deleteUrl = listItem.data('delete-url'); // Get the delete URL from data attribute

        console.log("Delete button click detected for convo ID:", convoId);
        console.log("Delete URL:", deleteUrl);

        // Basic validation
        if (!convoId || !deleteUrl) {
            console.error("Missing convo ID or delete URL for delete action.");
            alert("Could not delete conversation due to missing information.");
            return; // Exit if data is missing
        }


        if (confirm('Are you sure you want to delete this conversation?')) {
            // Get CSRF token (assuming cookie method)
            const csrftoken = getCookie('csrftoken'); // Ensure getCookie function is defined

            $.ajax({
                url: deleteUrl, // Use the URL from the data attribute
                type: 'POST',
                headers: { 'X-CSRFToken': csrftoken }, // Send CSRF token in header

                success: function(response) {
                    console.log("Delete request success:", response);
                    if (response.success) {
                        // Remove the list item from the DOM
                        listItem.remove();
                        console.log(`Conversation ${convoId} removed from recents.`);

                        // Handle redirect if a redirect URL is provided by the backend
                        if (response.redirect_url) {
                             console.log("Redirecting to:", response.redirect_url);
                             window.location.href = response.redirect_url;
                        } else {
                            // Optional fallback: if no redirect URL and the deleted item was active,
                            // you might want to redirect to the base assistant page or the latest remaining.
                            // However, the backend should ideally provide the redirect_url.
                            console.warn("Delete success but no redirect_url received from backend.");
                            // If no convos remain and no redirect, display the "No conversations yet" message
                             if (recentsList.find('li').length === 0) {
                                  $('.no-convos-msg').show(); // Assuming you have this element and it's hidden by default
                                   // Optionally, clear the chat panel if no convos remain
                                  chatBox.empty(); // Clear chat messages
                             }
                        }

                    } else {
                        alert('Error deleting conversation: ' + (response.error || 'Unknown error'));
                    }
                },
                error: function(xhr, status, error) {
                    console.error("Delete request failed:", status, error, xhr);
                    let errorMessage = 'An error occurred while trying to delete the conversation.';
                     if (xhr.status === 403) {
                         errorMessage = 'You do not have permission to delete this conversation.';
                     } else if (xhr.responseJSON && xhr.responseJSON.error) {
                         errorMessage = 'Error: ' + xhr.responseJSON.error;
                     } else {
                          errorMessage += ` Status: ${xhr.status}`;
                     }
                    alert(errorMessage);
                }
            });
        } else {
            console.log("Delete action cancelled by user.");
        }
    })

    $('.new-task-btn').on('click', function(e) {
        currentConversationId = null;
        e.preventDefault();
        window.location.href = '/agent/assistant/new-placeholder/';
    });

    // Animate the recents list title if just created
    const justCreatedLi = $('#recents-list li.just-created');
    if (justCreatedLi.length) {
        const link = justCreatedLi.find('.recent-link');
        const fullTitle = link.text();
        link.text('');
        let i = 0;
        function typeTitle() {
            if (i < fullTitle.length) {
                link.append(fullTitle.charAt(i));
                i++;
                setTimeout(typeTitle, 30); // Adjust speed as needed
            }
        }
        typeTitle();
        justCreatedLi.removeClass('just-created'); // Remove marker after animation
    }

}); // End of $(document).ready(...)


// Ensure your typeWriter function (if defined outside ready) is accessible or the logic is within ready
// Ensure sendUserMessage function is correctly defined and accessible.
// Ensure getCookie function is defined and accessible.


