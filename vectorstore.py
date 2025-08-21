# fogo_companion/vectorstore.py
import os
import pickle

class VectorStore:
    def __init__(self, store_dir):
        self.store_dir = store_dir
        self.meta_file = os.path.join(store_dir, "meta.pkl")
        os.makedirs(store_dir, exist_ok=True)

        self.metadata = []

        if self.exists():
            self._load()

    def exists(self):
        """Check if metadata exist."""
        return os.path.exists(self.meta_file)

    def index_is_empty(self):
        """Return True if the metadata is empty."""
        return len(self.metadata) == 0

    def _load(self):
        """Load metadata."""
        try:
            with open(self.meta_file, "rb") as f:
                self.metadata = pickle.load(f)
        except Exception as e:
            print(f"Warning: Could not load vector store. It might be rebuilt. Error: {e}")
            self.metadata = []


    def save(self):
        """Save metadata."""
        with open(self.meta_file, "wb") as f:
            pickle.dump(self.metadata, f)

    def add_texts(self, texts, metadatas):
        """Add texts and their metadata."""
        if not texts:
            return
            
        self.metadata.extend(metadatas)
        self.save()

    def remove_by_sources(self, sources_to_remove):
        """
        Remove all metadata entries where 'source' matches any in sources_to_remove.
        """
        if not sources_to_remove or not self.metadata:
            return

        sources_to_remove = set(sources_to_remove)
        
        # Keep entries not in sources_to_remove
        self.metadata = [m for m in self.metadata if m.get("source") not in sources_to_remove]

        self.save()
        print(f"[VectorStore] Removal complete. Now has {len(self.metadata)} entries.")