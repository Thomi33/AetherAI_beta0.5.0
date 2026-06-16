import { useState } from 'react';
import { motion } from 'framer-motion';
import Sidebar from './components/sidebar/Sidebar';
import Header from './components/header/Header';
import ChatPage from './pages/ChatPage';
import './App.css';

export default function App() {
  const [sidebarOpen, setSidebarOpen] = useState(true);

  return (
    <div className="flex h-screen bg-white dark:bg-neutral-950 text-neutral-900 dark:text-neutral-100">
      {/* Sidebar */}
      <motion.div
        initial={false}
        animate={{ width: sidebarOpen ? 260 : 0 }}
        transition={{ duration: 0.3, ease: 'easeInOut' }}
        className="overflow-hidden border-r border-neutral-200 dark:border-neutral-800"
      >
        <Sidebar onToggle={() => setSidebarOpen(!sidebarOpen)} />
      </motion.div>

      {/* Main Content */}
      <div className="flex-1 flex flex-col">
        <Header sidebarOpen={sidebarOpen} onToggleSidebar={() => setSidebarOpen(!sidebarOpen)} />
        <ChatPage />
      </div>
    </div>
  );
}
