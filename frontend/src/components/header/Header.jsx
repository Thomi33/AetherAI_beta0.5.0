import { motion } from 'framer-motion';
import { Menu, Moon, Sun } from 'lucide-react';
import { useState } from 'react';

export default function Header({ sidebarOpen, onToggleSidebar }) {
  const [darkMode, setDarkMode] = useState(false);

  const toggleDarkMode = () => {
    setDarkMode(!darkMode);
    document.documentElement.classList.toggle('dark');
  };

  return (
    <motion.header
      initial={{ y: -10, opacity: 0 }}
      animate={{ y: 0, opacity: 1 }}
      className="border-b border-neutral-200 dark:border-neutral-800 bg-white dark:bg-neutral-900 px-6 py-4 flex items-center justify-between"
    >
      <div className="flex items-center gap-4">
        <button
          onClick={onToggleSidebar}
          className="p-2 hover:bg-neutral-100 dark:hover:bg-neutral-800 rounded-lg transition-colors"
        >
          <Menu size={20} className="text-neutral-600 dark:text-neutral-400" />
        </button>

        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.1 }}
        >
          <h1 className="text-lg font-semibold">
            Aether<span className="text-blue-500">.</span>
          </h1>
          <p className="text-xs text-neutral-500 dark:text-neutral-400">Agente Local Inteligente</p>
        </motion.div>
      </div>

      <motion.button
        whileHover={{ scale: 1.05 }}
        whileTap={{ scale: 0.95 }}
        onClick={toggleDarkMode}
        className="p-2 hover:bg-neutral-100 dark:hover:bg-neutral-800 rounded-lg transition-colors"
      >
        {darkMode ? (
          <Sun size={20} className="text-yellow-500" />
        ) : (
          <Moon size={20} className="text-neutral-600" />
        )}
      </motion.button>
    </motion.header>
  );
}
