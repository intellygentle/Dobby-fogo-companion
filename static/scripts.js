// fogo_companion/static/scripts.js
document.addEventListener("DOMContentLoaded", () => {
    const chatBox = document.getElementById("chat-box");
    const userInput = document.getElementById("user-input");
    const sendBtn = document.getElementById("send-btn");
    const projectSelect = document.getElementById("project-select");
    const apiKeyCard = document.getElementById("api-key-card");
    const apiKeyInput = document.getElementById("api-key-input");
    const apiKeyError = document.getElementById("api-key-error");
    const overlay = document.getElementById("overlay");

    const converter = new showdown.Converter({
        simplifiedAutoLink: true,
        tables: true,
        strikethrough: true
    });

    // Check for stored API key
    let apiKey = localStorage.getItem('fireworksApiKey');
    if (!apiKey) {
        showApiKeyCard();
    } else {
        // Validate stored API key
        validateApiKey(apiKey).then(isValid => {
            if (isValid) {
                hideApiKeyCard();
            } else {
                localStorage.removeItem('fireworksApiKey');
                showApiKeyCard();
                apiKeyError.textContent = "Stored API key is invalid. Please enter a valid Fireworks AI API key.";
                apiKeyError.style.display = 'block';
            }
        }).catch(error => {
            console.error("Error validating stored API key:", error);
            localStorage.removeItem('fireworksApiKey');
            showApiKeyCard();
            apiKeyError.textContent = "Error validating stored API key. Please enter a valid Fireworks AI API key.";
            apiKeyError.style.display = 'block';
        });
    }

    function showApiKeyCard() {
        apiKeyCard.style.display = 'block';
        overlay.style.display = 'block';
        userInput.disabled = true;
        sendBtn.disabled = true;
        apiKeyInput.value = ''; // Clear input on show
        apiKeyInput.focus();
    }

    function hideApiKeyCard() {
        apiKeyCard.style.display = 'none';
        overlay.style.display = 'none';
        userInput.disabled = false;
        sendBtn.disabled = false;
        apiKeyError.style.display = 'none';
    }

    async function validateApiKey(key) {
        // Check format: starts with 'fw_', followed by 20+ alphanumeric characters
        const apiKeyPattern = /^fw_[A-Za-z0-9]{20,}$/;
        if (!apiKeyPattern.test(key)) {
            return false;
        }

        // Test API key with a silent test message to the LLM bot
        try {
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), 5000); // 5-second timeout
            const response = await fetch("/send_message", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ message: "test", api_key: key }),
                signal: controller.signal
            });
            clearTimeout(timeoutId);
            if (!response.ok) {
                return false;
            }
            const data = await response.json();
            return !!data.response; // Ensure a valid response was received
        } catch (error) {
            console.error("API key validation error:", error);
            return false;
        }
    }

    window.saveApiKey = async function() {
        const key = apiKeyInput.value.trim();
        apiKeyError.style.display = 'none';

        if (!key) {
            apiKeyError.textContent = "Please enter a Fireworks AI API key.";
            apiKeyError.style.display = 'block';
            apiKeyInput.focus();
            return;
        }

        apiKeyCard.style.pointerEvents = 'none'; // Disable interactions during validation
        apiKeyCard.style.opacity = '0.7'; // Visual feedback during validation
        apiKeyError.textContent = "Validating API key...";
        apiKeyError.style.display = 'block';

        try {
            const isValid = await validateApiKey(key);
            apiKeyCard.style.pointerEvents = 'auto';
            apiKeyCard.style.opacity = '1';
            if (isValid) {
                localStorage.setItem('fireworksApiKey', key);
                apiKey = key;
                hideApiKeyCard();
                userInput.focus();
            } else {
                apiKeyError.textContent = "Invalid Fireworks AI API key. It should start with 'fw_' and be followed by at least 20 alphanumeric characters, or the key is not authorized. Please try again.";
                apiKeyError.style.display = 'block';
                apiKeyInput.value = ''; // Clear input for retry
                apiKeyInput.focus();
            }
        } catch (error) {
            console.error("Error during API key validation:", error);
            apiKeyCard.style.pointerEvents = 'auto';
            apiKeyCard.style.opacity = '1';
            apiKeyError.textContent = "Error validating API key. Please check your connection and try again.";
            apiKeyError.style.display = 'block';
            apiKeyInput.value = ''; // Clear input for retry
            apiKeyInput.focus();
        }
    };

    function addMessage(message, sender) {
        const messageElement = document.createElement("div");
        messageElement.classList.add("message", sender === "user" ? "user-message" : "bot-message");
        
        if (sender === "bot") {
            const html = converter.makeHtml(message);
            messageElement.innerHTML = html;

            // Add Tweet button
            const tweetButton = document.createElement("button");
            tweetButton.className = "tweet-btn";
            tweetButton.textContent = "Tweet";
            tweetButton.onclick = () => {
                const tweetText = messageElement.textContent.replace("Tweet", "").trim();
                const tweetUrl = `https://twitter.com/intent/tweet?text=${encodeURIComponent(tweetText)}`;
                window.open(tweetUrl, "_blank");
            };
            messageElement.appendChild(tweetButton);
        } else {
            const p = document.createElement("p");
            p.textContent = message;
            messageElement.appendChild(p);
        }

        chatBox.appendChild(messageElement);
        chatBox.scrollTop = chatBox.scrollHeight;
    }

    async function sendMessage() {
        if (!apiKey) {
            showApiKeyCard();
            return;
        }

        const message = userInput.value.trim();
        if (message === "") return;

        addMessage(message, "user");
        userInput.value = "";
        // Add a "thinking" message
        const thinkingElement = document.createElement("div");
        thinkingElement.classList.add("message", "bot-message");
        thinkingElement.innerHTML = "<p><em>Fogo is thinking...</em></p>";
        chatBox.appendChild(thinkingElement);
        chatBox.scrollTop = chatBox.scrollHeight;

        const payload = { message, api_key: apiKey };
        if (projectSelect && projectSelect.value) {
            payload.project = projectSelect.value;
        }

        try {
            const response = await fetch("/send_message", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });
            // Remove the "thinking" message
            if (chatBox.contains(thinkingElement)) {
                chatBox.removeChild(thinkingElement);
            }
            if (!response.ok) {
                if (response.status === 401 || response.status === 403) {
                    localStorage.removeItem('fireworksApiKey');
                    apiKey = null;
                    showApiKeyCard();
                    apiKeyError.textContent = "API key is no longer valid. Please enter a new Fireworks AI API key.";
                    apiKeyError.style.display = 'block';
                    return;
                }
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            const data = await response.json();
            if (data.response) {
                addMessage(data.response, "bot");
            } else {
                addMessage("I seem to be at a loss for words. Please try again.", "bot");
            }

        } catch (err) {
            // Ensure thinking message is removed on error too
            if (chatBox.contains(thinkingElement)) {
                chatBox.removeChild(thinkingElement);
            }
            console.error("Error:", err);
            addMessage("Sorry, I'm having trouble connecting. Please check the console and try again.", "bot");
        }
    }

    sendBtn.addEventListener("click", sendMessage);
    userInput.addEventListener("keypress", (e) => {
        if (e.key === "Enter") {
            e.preventDefault();
            sendMessage();
        }
    });

    // Handle topic button clicks
    document.querySelectorAll('.topic-btn').forEach(button => {
        button.addEventListener('click', () => {
            userInput.value = button.textContent;
            userInput.focus();
            sendMessage();
        });
    });
});