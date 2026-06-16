import { motion } from 'framer-motion';
import { Plus, Trash2, Settings } from 'lucide-react';

export default function Sidebar({ onToggle }) {
  const conversations = [
    { id: 1, title: 'Nueva conversación' },
    { id: 2, title: 'Pregunta sobre Python' },
    { id: 3, title: 'Análisis de datos' },
  ];

  return (
    <div className="w-64 h-screen bg-white dark:bg-neutral-900 flex flex-col border-r border-neutral-200 dark:border-neutral-800">
      {/* Header */}
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.1 }}
        className="p-4 border-b border-neutral-200 dark:border-neutral-800"
      >
        <button className="w-full flex items-center justify-center gap-2 px-4 py-2 bg-neutral-100 dark:bg-neutral-800 hover:bg-neutral-200 dark:hover:bg-neutral-700 rounded-lg transition-colors">
          <Plus size={20} />
          <span className="text-sm font-medium">Nuevo chat</span>
        </button>
      </motion.div>

      {/* Conversations List */}
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.2 }}
        className="flex-1 overflow-y-auto p-3"
      >
        <h3 className="text-xs font-semibold text-neutral-500 dark:text-neutral-400 uppercase px-3 py-2 mb-2">
          Historial
        </h3>
        <div className="space-y-2">
          {conversations.map((conv, index) => (
            <motion.button
              key={conv.id}
              initial={{ opacity: 0, x: -10 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: 0.2 + index * 0.05 }}
              className="w-full text-left px-3 py-2 rounded-lg hover:bg-neutral-100 dark:hover:bg-neutral-800 transition-colors group"
            >
              <p className="text-sm truncate">{conv.title}</p>
              <div className="flex gap-1 mt-1 opacity-0 group-hover:opacity-100 transition-opacity">
                <button className="p-1 hover:bg-neutral-200 dark:hover:bg-neutral-700 rounded text-xs text-neutral-500">
                  <Trash2 size={14} />
                </button>
              </div>
            </motion.button>
          ))}
        </div>
      </motion.div>

      {/* Footer */}
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.3 }}
        className="p-4 border-t border-neutral-200 dark:border-neutral-800"
      >
        <button className="w-full flex items-center justify-center gap-2 px-4 py-2 text-neutral-600 dark:text-neutral-400 hover:bg-neutral-100 dark:hover:bg-neutral-800 rounded-lg transition-colors">
          <Settings size={18} />
          <span className="text-sm">Configuración</span>
        </button>
      </motion.div>
    </div>
  );
}
